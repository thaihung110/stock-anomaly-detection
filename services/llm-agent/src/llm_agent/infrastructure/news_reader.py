"""News retrieval — union bronze (fresh tail) + silver (historical body).

Bronze catalog: raw.raw_news_articles   (Finnhub streaming, ~30s lag)
Silver catalog: normalized.news_clean   (NewsAPI batch, cleaned+deduped)

The union → dedup → top-K strategy gives the LLM both real-time context
(breaking news within the lookback window) and historical depth (story arcs
that started days ago).  Articles shared between both sources are deduped on
md5(title) so the prompt never contains duplicate snippets.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual
from pyiceberg.utils.datetime import datetime_to_micros

from llm_agent.config import Settings

logger = structlog.get_logger(__name__)


def _published_after(symbol: str, cutoff: datetime) -> And:
    """Build a PyIceberg predicate: symbol == :symbol AND published_at >= :cutoff.

    Uses the typed expression API (not a string row_filter) so the timestamp
    literal is bound to the column type by PyIceberg.  This is robust against
    the column being either ``timestamp`` or ``timestamptz`` — passing the epoch
    micros (int) converts cleanly to both, avoiding ISO-8601 string-format bugs
    (e.g. "Z" vs "+00:00", missing zone offset).
    """
    return And(
        EqualTo("symbol", symbol),
        GreaterThanOrEqual("published_at", datetime_to_micros(cutoff)),
    )

# Columns to select per source.  Mapped to canonical keys: title / url / source / published_at.
# Verified against Spark schema in news-ingest-stream/NewsSchema.scala (bronze)
# and news-cleaner/NewsCleanerPipeline.scala (silver) — both use "url", not "article_url".
_FRESH_COLS = ("symbol", "title", "url", "source_name", "published_at", "description")
_HIST_COLS = ("symbol", "title", "url", "source_name", "published_at", "description")


def _catalog_kwargs(name: str, warehouse: str, cfg: Settings) -> dict[str, str]:
    """Build PyIceberg REST catalog init kwargs for the given warehouse."""
    return {
        "type": "rest",
        "uri": cfg.iceberg_catalog_uri,
        "rest.auth.type": "oauth2",
        "oauth2-server-uri": cfg.iceberg_oauth2_server_uri,
        "credential": cfg.iceberg_oauth2_credential,
        "scope": cfg.iceberg_oauth2_scope,
        "token-exchange-enabled": "false",
        "warehouse": warehouse,
        "header.X-Iceberg-Access-Delegation": "",
        "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
        "s3.endpoint": cfg.s3_endpoint,
        "s3.access-key-id": cfg.s3_access_key_id,
        "s3.secret-access-key": cfg.s3_secret_access_key,
        "s3.region": cfg.s3_region,
        "s3.path-style-access": str(cfg.s3_path_style_access).lower(),
    }


def _build_catalog(name: str, warehouse: str, cfg: Settings) -> Any:
    from pyiceberg.catalog import load_catalog  # lazy import — optional heavy dep

    return load_catalog(name, **_catalog_kwargs(name, warehouse, cfg))


def _arrow_to_articles(arrow_table: Any, col_map: dict[str, str]) -> list[dict[str, str | None]]:
    """Convert a PyArrow table to a list of canonical article dicts.

    col_map: {source_column_name → canonical_key}
    Canonical keys: title, url, source, published_at
    """
    schema_names: set[str] = set(arrow_table.schema.names)
    articles: list[dict[str, str | None]] = []
    for i in range(arrow_table.num_rows):
        row: dict[str, str | None] = {}
        for src_col, dst_key in col_map.items():
            if src_col in schema_names:
                val = arrow_table.column(src_col)[i].as_py()
                row[dst_key] = str(val) if val is not None else None
            else:
                row[dst_key] = None
        articles.append(row)
    return articles


def _dedup_key(article: dict[str, str | None]) -> str:
    """Stable dedup key: url if present, else md5(title)."""
    url = (article.get("url") or "").strip()
    if url:
        return url
    title = (article.get("title") or "").strip()
    return hashlib.md5(title.encode()).hexdigest()  # noqa: S324 — non-crypto use


def _fetch_fresh(symbol: str, cutoff: datetime, cfg: Settings) -> list[dict[str, str | None]]:
    """Fetch recent articles from bronze catalog (raw.raw_news_articles)."""
    catalog = _build_catalog(cfg.bronze_catalog_name, cfg.bronze_warehouse, cfg)
    table = catalog.load_table(cfg.news_table)
    arrow = table.scan(
        selected_fields=_FRESH_COLS,
        row_filter=_published_after(symbol, cutoff),
    ).to_arrow()
    return _arrow_to_articles(
        arrow,
        {
            "title": "title",
            "url": "url",
            "source_name": "source",
            "published_at": "published_at",
        },
    )


def _fetch_historical(
    symbol: str, cutoff: datetime, cfg: Settings
) -> list[dict[str, str | None]]:
    """Fetch historical articles from silver catalog (normalized.news_clean)."""
    catalog = _build_catalog(cfg.silver_catalog_name, cfg.silver_warehouse, cfg)
    table = catalog.load_table(cfg.news_digest_table)
    arrow = table.scan(
        selected_fields=_HIST_COLS,
        row_filter=_published_after(symbol, cutoff),
    ).to_arrow()
    return _arrow_to_articles(
        arrow,
        {
            "title": "title",
            "url": "url",
            "source_name": "source",
            "published_at": "published_at",
        },
    )


def fetch_news(symbol: str, cfg: Settings) -> list[dict[str, str | None]]:
    """Return top-K deduplicated articles for *symbol* from both catalogs.

    Strategy:
      1. Fresh tail  : bronze / raw.raw_news_articles, within NEWS_LOOKBACK_HOURS
      2. Historical  : silver / normalized.news_clean, within NEWS_LOOKBACK_DAYS
      3. Union → dedup(md5(title)|url) → sort published_at DESC → top-K

    Each returned dict has keys: title, url, source, published_at (all str|None).
    Errors in either catalog are logged as warnings; the other source is still used.
    """
    now = datetime.now(tz=timezone.utc)
    fresh_cutoff = now - timedelta(hours=cfg.news_lookback_hours)
    hist_cutoff = now - timedelta(days=cfg.news_lookback_days)

    fresh: list[dict[str, str | None]] = []
    historical: list[dict[str, str | None]] = []

    try:
        fresh = _fetch_fresh(symbol, fresh_cutoff, cfg)
        logger.info("news_fresh_fetched", symbol=symbol, count=len(fresh))
    except Exception as exc:
        logger.warning("news_fresh_fetch_failed", symbol=symbol, error=str(exc))

    try:
        historical = _fetch_historical(symbol, hist_cutoff, cfg)
        logger.info("news_hist_fetched", symbol=symbol, count=len(historical))
    except Exception as exc:
        logger.warning("news_hist_fetch_failed", symbol=symbol, error=str(exc))

    # Union: fresh first so newer articles take precedence in dedup
    seen: set[str] = set()
    merged: list[dict[str, str | None]] = []
    for article in fresh + historical:
        key = _dedup_key(article)
        if key not in seen:
            seen.add(key)
            merged.append(article)

    merged.sort(key=lambda a: a.get("published_at") or "", reverse=True)
    top_k = merged[: cfg.news_top_k]
    logger.info("news_merged", symbol=symbol, total=len(merged), top_k=len(top_k))
    return top_k
