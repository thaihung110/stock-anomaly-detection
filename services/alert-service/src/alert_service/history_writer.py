"""Iceberg writer for ``gold.fact_alert_history``.

The catalog connection and table handle are initialised once at service startup
via :func:`init_iceberg` and reused across all alert writes.  This avoids an
OAuth2 token-exchange and catalog metadata round-trip on every single write,
which under fan-out (one append per recipient) would quickly exhaust the 10 s
timeout and flood Keycloak with auth requests.

If the cached ``Table`` object raises on ``append()``, the writer attempts one
reload of the table handle before propagating the error.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.table import Table

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
        pa.field("user_id", pa.string(), nullable=True),
    ]
)

_ICEBERG_WRITE_TIMEOUT_SEC: float = 10.0

# Module-level singletons — populated by init_iceberg() during lifespan.
_catalog: Catalog | None = None
_table: Table | None = None
_cfg_ref: Settings | None = None


def init_iceberg(cfg: Settings) -> None:
    """Load the Iceberg catalog and table handle once at startup.

    Must be called before the first :func:`append_alert_history` call.
    Safe to call multiple times (idempotent — reloads on each call so
    a crash-loop restart picks up fresh credentials).
    """
    global _catalog, _table, _cfg_ref
    _cfg_ref = cfg
    _catalog = load_catalog(
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
    _table = _catalog.load_table(cfg.fact_alert_history_table)
    logger.info(
        "iceberg_initialized",
        catalog=cfg.iceberg_catalog_name,
        table=cfg.fact_alert_history_table,
    )


def _reload_table() -> Table:
    """Reload the table handle from the cached catalog (no auth round-trip)."""
    if _catalog is None or _cfg_ref is None:
        raise RuntimeError("init_iceberg() must be called during service startup before any writes")
    global _table
    _table = _catalog.load_table(_cfg_ref.fact_alert_history_table)
    return _table


def _alert_to_arrow(alert: AlertEvent, user_id: str | None) -> pa.Table:
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
            "user_id": [user_id],
        },
        schema=_SCHEMA,
    )


def _append_to_iceberg(alert: AlertEvent, user_id: str | None) -> None:
    if _table is None:
        raise RuntimeError("init_iceberg() must be called during service startup before any writes")
    arrow_table = _alert_to_arrow(alert, user_id)
    try:
        _table.append(arrow_table)
    except Exception:
        # Stale table handle (e.g., server restart) — reload once and retry.
        fresh = _reload_table()
        fresh.append(arrow_table)
    logger.info(
        "alert_history_written",
        alert_id=alert.alert_id,
        symbol=alert.symbol,
        user_id=user_id,
    )


async def append_alert_history(
    alert: AlertEvent,
    _cfg: Settings,  # retained for call-site compatibility; unused after init_iceberg()
    user_id: str | None = None,
) -> None:
    """Append a single alert row to ``gold.fact_alert_history`` (non-blocking).

    Args:
        alert: Alert payload from Kafka.
        _cfg: Unused — retained for call-site compatibility. Iceberg config is
            applied once at startup via :func:`init_iceberg`.
        user_id: Recipient user_id. ``None`` produces a NULL ``user_id`` row
            (admin-chat fallback / legacy behavior).

    Raises:
        TimeoutError: Iceberg write exceeded ``_ICEBERG_WRITE_TIMEOUT_SEC``.
        Exception: Any I/O or catalog error propagates so callers can route
            the failure to the DLQ.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_append_to_iceberg, alert, user_id),
            timeout=_ICEBERG_WRITE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(
            "history_write_timeout",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            user_id=user_id,
            timeout_sec=_ICEBERG_WRITE_TIMEOUT_SEC,
        )
        raise
    except Exception as exc:
        logger.error(
            "history_write_failed",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            user_id=user_id,
            error=str(exc),
        )
        raise
