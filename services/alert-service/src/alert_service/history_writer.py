"""Iceberg writer for ``gold.fact_alert_history``.

The catalog connection and table handle are initialised once at service startup
via :func:`init_iceberg` and reused across all alert writes.  This avoids an
OAuth2 token-exchange and catalog metadata round-trip on every single write.

All writes are serialised through a single-worker ``ThreadPoolExecutor`` so
there is never more than one concurrent Iceberg commit in flight.  This
eliminates the ``CommitFailedException`` race that occurred when fan-out
dispatched N concurrent ``asyncio.to_thread`` appends on the same ``Table``
object (Bug #2), and naturally batches all recipients for one alert into a
single commit (Bug #6).

``asyncio.wait_for`` cannot cancel a running thread, so on timeout the commit
outcome is *unknown* — the background thread may still complete.  Callers
**must not** DLQ-and-replay on ``asyncio.TimeoutError`` from this module to
avoid writing duplicate rows into the append-only table (Bug #3).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
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
# Single-worker executor serialises all Iceberg commits; never more than one
# blocking write runs at a time.
_write_executor: concurrent.futures.ThreadPoolExecutor | None = None


def init_iceberg(cfg: Settings) -> None:
    """Load the Iceberg catalog and table handle once at startup.

    Must be called before the first write.  Safe to call multiple times
    (idempotent — reloads on each call so a crash-loop restart picks up fresh
    credentials).  Replaces any previously created write executor.
    """
    global _catalog, _table, _cfg_ref, _write_executor
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
    if _write_executor is not None:
        _write_executor.shutdown(wait=False)
    _write_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="iceberg-writer"
    )
    logger.info(
        "iceberg_initialized",
        catalog=cfg.iceberg_catalog_name,
        table=cfg.fact_alert_history_table,
    )


def close_iceberg() -> None:
    """Drain the write executor gracefully.  Call during lifespan shutdown.

    Blocks until any in-flight Iceberg commit completes so the process does not
    exit mid-write.  Safe to call if ``init_iceberg()`` was never called.
    """
    global _write_executor
    if _write_executor is not None:
        _write_executor.shutdown(wait=True)
        _write_executor = None
        logger.info("iceberg_executor_closed")


def _reload_table() -> Table:
    """Reload the table handle from the cached catalog (no auth round-trip)."""
    if _catalog is None or _cfg_ref is None:
        raise RuntimeError("init_iceberg() must be called during service startup before any writes")
    global _table
    _table = _catalog.load_table(_cfg_ref.fact_alert_history_table)
    return _table


def _build_arrow_table(alert: AlertEvent, user_ids: list[str | None]) -> pa.Table:
    n = len(user_ids)
    now = datetime.now(timezone.utc).isoformat()
    return pa.table(
        {
            "alert_id": [alert.alert_id] * n,
            "symbol": [alert.symbol] * n,
            "event_ts": [alert.event_ts] * n,
            "rule_name": [alert.rule_name.value] * n,
            "severity": [alert.severity.value] * n,
            "triggered_value": [alert.triggered_value] * n,
            "threshold": [alert.threshold] * n,
            "alert_source": ["system"] * n,
            "written_at": [now] * n,
            "user_id": list(user_ids),
        },
        schema=_SCHEMA,
    )


def _write_batch(alert: AlertEvent, user_ids: list[str | None]) -> None:
    """Blocking Iceberg append — runs exclusively in the single-worker executor."""
    if _table is None:
        raise RuntimeError("init_iceberg() must be called during service startup before any writes")
    arrow = _build_arrow_table(alert, user_ids)
    try:
        _table.append(arrow)
    except Exception:
        # Stale table handle (e.g., catalog server restart) — reload once and retry.
        fresh = _reload_table()
        fresh.append(arrow)
    logger.info(
        "alert_history_batch_written",
        alert_id=alert.alert_id,
        symbol=alert.symbol,
        recipient_count=len(user_ids),
    )


async def append_alert_history_batch(
    alert: AlertEvent,
    _cfg: Settings,
    user_ids: list[str | None],
) -> None:
    """Append one row per ``user_id`` in a single Iceberg commit (non-blocking).

    Args:
        alert: Alert payload from Kafka.
        _cfg: Unused — retained for call-site symmetry with
            :func:`append_alert_history`.
        user_ids: One entry per recipient.  ``None`` produces a NULL
            ``user_id`` row (admin-chat fallback).

    Raises:
        asyncio.TimeoutError: Commit exceeded ``_ICEBERG_WRITE_TIMEOUT_SEC``.
            **Do not DLQ on this error** — the background thread may still
            commit the row, and re-delivery would produce a duplicate row in
            the append-only table.
        RuntimeError: :func:`init_iceberg` was not called.
        Exception: Any Iceberg / S3 error after the stale-handle retry.
    """
    if _write_executor is None:
        raise RuntimeError("init_iceberg() must be called during service startup before any writes")
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                _write_executor,
                functools.partial(_write_batch, alert, list(user_ids)),
            ),
            timeout=_ICEBERG_WRITE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.error(
            "history_write_timeout_unknown_state",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            timeout_sec=_ICEBERG_WRITE_TIMEOUT_SEC,
        )
        raise
    except Exception as exc:
        logger.error(
            "history_write_failed",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            error=str(exc),
        )
        raise


async def append_alert_history(
    alert: AlertEvent,
    _cfg: Settings,
    user_id: str | None = None,
) -> None:
    """Single-recipient wrapper over :func:`append_alert_history_batch`.

    Retained for the legacy (admin-only) path in ``main.py``.
    """
    await append_alert_history_batch(alert, _cfg, user_ids=[user_id])
