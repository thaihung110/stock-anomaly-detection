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
    arrow_table = table.scan(
        selected_fields=("symbol", *_CONTEXT_FIELDS)
    ).to_arrow()

    context: dict[str, dict[str, float]] = {}
    for i in range(arrow_table.num_rows):
        symbol: str = arrow_table.column("symbol")[i].as_py().upper()
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
