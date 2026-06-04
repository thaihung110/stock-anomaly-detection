"""
Finnhub REST client for /company-news endpoint.

Polls all configured symbols once per cycle. Applies:
- 1.1s delay between per-symbol requests to stay under 60 req/min free-tier cap
- Single retry with 5s backoff on HTTP 429 or 5xx
- In-memory MD5 dedup set to skip articles already published this session
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx
import structlog

from finnhub_news_producer.config import Settings

logger = structlog.get_logger(__name__)

_BASE_URL = "https://finnhub.io/api/v1/company-news"


async def _fetch_symbol_news(
    client: httpx.AsyncClient,
    symbol: str,
    from_date: str,
    to_date: str,
    api_key: str,
) -> list[dict]:
    params = {"symbol": symbol, "from": from_date, "to": to_date, "token": api_key}

    for attempt in range(2):
        try:
            resp = await client.get(_BASE_URL, params=params, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503):
                logger.warning(
                    "news_api_retrying",
                    symbol=symbol,
                    status=resp.status_code,
                    attempt=attempt + 1,
                )
                await asyncio.sleep(5.0)
                continue
            logger.error("news_api_unexpected_status", symbol=symbol, status=resp.status_code)
            return []
        except httpx.RequestError as exc:
            logger.warning("news_api_request_error", symbol=symbol, error=str(exc), attempt=attempt + 1)
            if attempt == 0:
                await asyncio.sleep(5.0)

    return []


async def poll_news(
    config: Settings,
    seen_ids: set[str],
) -> AsyncIterator[tuple[str, dict]]:
    """
    Async generator that yields (symbol, raw_article_dict) for unseen articles.

    Iterates all configured symbols once per call. Caller should invoke this
    once per poll cycle and sleep poll_interval_sec between cycles.
    """
    to_date = date.today()
    from_date = to_date - timedelta(days=config.lookback_days)
    from_str = from_date.isoformat()
    to_str = to_date.isoformat()

    async with httpx.AsyncClient() as client:
        for symbol in config.symbols_list:
            raw_articles = await _fetch_symbol_news(
                client, symbol, from_str, to_str, config.finnhub_api_key
            )

            for raw in raw_articles:
                url = raw.get("url", "")
                if not url:
                    continue

                # Compute dedup key using same MD5 logic as schema
                import hashlib
                article_id = hashlib.md5(url.encode("utf-8")).hexdigest()  # noqa: S324

                if article_id in seen_ids:
                    continue

                seen_ids.add(article_id)

                # Prevent unbounded growth of dedup set
                if len(seen_ids) > config.dedup_max_size:
                    seen_ids.clear()
                    seen_ids.add(article_id)
                    logger.info("dedup_set_cleared", reason="max_size_reached")

                yield symbol, raw

            await asyncio.sleep(config.request_delay_sec)
