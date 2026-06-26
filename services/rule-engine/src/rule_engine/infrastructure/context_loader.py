import structlog
from rule_engine.config import Settings

logger = structlog.get_logger(__name__)

_CONTEXT_FIELDS = (
    "mean_return_20d",
    "std_return_20d",
    "mean_return_5d",
    "std_return_5d",
    "mean_volume_20d",
    "std_volume_20d",
    "bb_upper_20d",
    "bb_lower_20d",
    "bb_mid_20d",
    "atr_14",
    "rsi_14",
    "vwap_5d_avg",
)

# Per-field NULL coercion overrides. Without this, rsi_14=NULL → 0.0 which
# satisfies rsi < 20 and fires a false oversold alert on every quote for
# symbols with insufficient history. Neutral RSI (50) skips both thresholds.
_CONTEXT_NULL_DEFAULTS: dict[str, float] = {
    "rsi_14": 50.0,
}


def load_context(cfg: Settings) -> dict[str, dict[str, float]]:
    """Load gold.rule_engine_context from Iceberg REST catalog (Gravitino).

    Returns:
        Dict keyed by uppercase symbol. Each value maps context field
        names to float values (nulls coerced to 0.0).
    """
    from pyiceberg.catalog import load_catalog

    catalog = load_catalog(
        cfg.iceberg_catalog_name,
        **{
            "type": "rest",
            "uri": cfg.iceberg_catalog_uri,
            "rest.auth.type": "oauth2",
            "oauth2-server-uri": cfg.iceberg_oauth2_server_uri,
            "credential": cfg.iceberg_oauth2_credential,
            "scope": cfg.iceberg_oauth2_scope,
            "token-exchange-enabled": "false",
            "warehouse": cfg.iceberg_warehouse,
            # Mirror Spark CatalogConfigurator: client-side S3 access, no credential vending.
            "header.X-Iceberg-Access-Delegation": "",
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
            "s3.endpoint": cfg.s3_endpoint,
            "s3.access-key-id": cfg.s3_access_key_id,
            "s3.secret-access-key": cfg.s3_secret_access_key,
            "s3.region": cfg.s3_region,
            "s3.path-style-access": str(cfg.s3_path_style_access).lower(),
        },
    )

    table = catalog.load_table(cfg.rule_engine_context_table)
    # rule_engine_context retains one partition per as_of_date (grain:
    # symbol x trading day). Scan all rows but keep only the most recent
    # as_of_date per symbol — otherwise the dict would be overwritten in
    # non-deterministic Iceberg scan order and may load stale baselines.
    arrow_table = table.scan(
        selected_fields=("symbol", "as_of_date", *_CONTEXT_FIELDS)
    ).to_arrow()

    context: dict[str, dict[str, float]] = {}
    latest_as_of: dict[str, object] = {}
    for i in range(arrow_table.num_rows):
        symbol: str = arrow_table.column("symbol")[i].as_py().upper()
        as_of_date = arrow_table.column("as_of_date")[i].as_py()

        prev = latest_as_of.get(symbol)
        if prev is not None and as_of_date <= prev:
            continue

        latest_as_of[symbol] = as_of_date
        context[symbol] = {
            field: float(
                v
                if (v := arrow_table.column(field)[i].as_py()) is not None
                else _CONTEXT_NULL_DEFAULTS.get(field, 0.0)
            )
            for field in _CONTEXT_FIELDS
        }

    logger.info("context_loaded", symbol_count=len(context))
    return context
