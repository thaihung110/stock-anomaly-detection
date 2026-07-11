"""Iceberg writer for ``gold.fact_alert_history`` (Phase 1 — instance-based).

Each ``HistoryWriter`` instance owns its own catalog/table/executor (see
:mod:`alert_service.infrastructure.iceberg.base_writer`) instead of module
globals, so ``AlertDeliveryService`` can be constructed with a real writer in
production and a fake/mock one in tests.

All writes are serialised through a single-worker executor so there is never
more than one concurrent Iceberg commit in flight. This eliminates the
``CommitFailedException`` race that occurred when fan-out dispatched N
concurrent ``asyncio.to_thread`` appends on the same ``Table`` object, and
naturally batches all recipients for one alert into a single commit.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import structlog
from pyiceberg.catalog import load_catalog

from alert_service.core.config import Settings
from alert_service.core.schema import AlertEvent
from alert_service.infrastructure.iceberg.base_writer import (
    BaseIcebergWriter,
    catalog_kwargs_from_settings,
)

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

_WRITE_TIMEOUT_SEC: float = 10.0


class HistoryWriter(BaseIcebergWriter):
    """Appends one row per recipient to ``gold.fact_alert_history``.

    Raises:
        TimeoutError: Commit exceeded ``_WRITE_TIMEOUT_SEC``. **Do not DLQ on
            this error** — the background thread may still commit, and
            re-delivery would produce a duplicate row in the append-only table.
        RuntimeError: :meth:`init` was not called before a write.
        Exception: Any Iceberg / S3 error after the stale-handle retry.
    """

    def __init__(self) -> None:
        super().__init__(thread_name_prefix="iceberg-writer")

    def init(self, cfg: Settings) -> None:
        """Load the Iceberg catalog and table handle once at startup.

        Idempotent — safe to call multiple times (a crash-loop restart picks
        up fresh credentials); replaces any previously created write executor
        without waiting for it to drain.
        """
        iceberg = cfg.iceberg
        catalog = load_catalog(iceberg.catalog_name, **catalog_kwargs_from_settings(iceberg))
        self._finish_open(catalog, iceberg.fact_alert_history_table, drain_previous=False)
        logger.info(
            "iceberg_initialized",
            catalog=iceberg.catalog_name,
            table=iceberg.fact_alert_history_table,
        )

    def _build_arrow_table(self, alert: AlertEvent, user_ids: list[str | None]) -> pa.Table:
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

    async def append_batch(self, alert: AlertEvent, user_ids: list[str | None]) -> None:
        """Append one row per ``user_id`` in a single Iceberg commit (non-blocking).

        Args:
            alert: Alert payload from Kafka.
            user_ids: One entry per recipient. ``None`` produces a NULL
                ``user_id`` row (admin-chat fallback).
        """
        arrow = self._build_arrow_table(alert, list(user_ids))
        await self._write(
            arrow,
            timeout=_WRITE_TIMEOUT_SEC,
            timeout_event="history_write_timeout_unknown_state",
            failure_event="history_write_failed",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
        )
        logger.info(
            "alert_history_batch_written",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            recipient_count=len(user_ids),
        )

    async def append(self, alert: AlertEvent, user_id: str | None = None) -> None:
        """Single-recipient convenience wrapper over :meth:`append_batch`.

        Used by the admin-only (fan-out disabled) delivery path.
        """
        await self.append_batch(alert, user_ids=[user_id])
