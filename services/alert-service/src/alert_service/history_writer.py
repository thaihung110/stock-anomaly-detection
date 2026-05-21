import asyncio
from datetime import datetime, timezone

import pyarrow as pa
import structlog
from pyiceberg.catalog import load_catalog

from alert_service.config import Settings
from alert_service.schema import AlertEvent

logger = structlog.get_logger(__name__)

_SCHEMA = pa.schema(
    [
        pa.field("alert_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("event_ts", pa.string(), nullable=False),
        pa.field("rule_name", pa.string(), nullable=False),
        pa.field("severity", pa.string(), nullable=False),
        pa.field("triggered_value", pa.float64(), nullable=False),
        pa.field("threshold", pa.float64(), nullable=False),
        pa.field("alert_source", pa.string(), nullable=False),
        pa.field("written_at", pa.string(), nullable=False),
    ]
)


def _alert_to_arrow(alert: AlertEvent) -> pa.Table:
    now = datetime.now(timezone.utc).isoformat()
    return pa.table(
        {
            "alert_id": [alert.alert_id],
            "symbol": [alert.symbol],
            "event_ts": [alert.event_ts],
            "rule_name": [alert.rule_name.value],
            "severity": [alert.severity.value],
            "triggered_value": [alert.triggered_value],
            "threshold": [alert.threshold],
            "alert_source": ["system"],
            "written_at": [now],
        },
        schema=_SCHEMA,
    )


def _append_to_iceberg(alert: AlertEvent, cfg: Settings) -> None:
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
            "header.X-Iceberg-Access-Delegation": "",
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
            "s3.endpoint": cfg.s3_endpoint,
            "s3.access-key-id": cfg.s3_access_key_id,
            "s3.secret-access-key": cfg.s3_secret_access_key,
            "s3.region": cfg.s3_region,
            "s3.path-style-access": str(cfg.s3_path_style_access).lower(),
        },
    )
    table = catalog.load_table(cfg.fact_alert_history_table)
    arrow_table = _alert_to_arrow(alert)
    table.append(arrow_table)
    logger.info("alert_history_written", alert_id=alert.alert_id, symbol=alert.symbol)


async def append_alert_history(alert: AlertEvent, cfg: Settings) -> None:
    """Append a single alert row to gold.fact_alert_history (non-blocking)."""
    await asyncio.to_thread(_append_to_iceberg, alert, cfg)
