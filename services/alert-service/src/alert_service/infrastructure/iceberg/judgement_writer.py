"""Iceberg writer for gold.anomaly_judgement (Stage D — OPT-IN analytics).

Phase 1 — instance-based (see
:mod:`alert_service.infrastructure.iceberg.base_writer`): writes LLM judgement
records ONLY when ``DELIVERY_SOURCE=confirmed``; append-only event log, never
updates or deletes existing rows.

On service start with DELIVERY_SOURCE=raw: init() is a no-op; the table is
never created, existing rule-based flow is untouched.

Schema (gold.anomaly_judgement):
  alert_id, symbol, event_ts, rule_name, severity, judgement, news_category,
  llm_explanation, news_articles_found, revision, is_flip, written_at, event_date
See §11.5 of ai-agent-plan.md for the full DDL and dashboard queries.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa
import structlog
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import BooleanType, IntegerType, NestedField, StringType

from alert_service.core.config import DeliverySource, Settings
from alert_service.core.schema import ConfirmedAlertEvent, FollowUpEvent
from alert_service.infrastructure.iceberg.base_writer import (
    BaseIcebergWriter,
    catalog_kwargs_from_settings,
)

logger = structlog.get_logger(__name__)

# Column order and types must match the DDL in §11.5.
_ARROW_SCHEMA = pa.schema(
    [
        pa.field("alert_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("event_ts", pa.string(), nullable=False),
        pa.field("rule_name", pa.string(), nullable=False),
        pa.field("severity", pa.string(), nullable=True),
        pa.field("judgement", pa.string(), nullable=False),
        pa.field("news_category", pa.string(), nullable=True),
        pa.field("llm_explanation", pa.string(), nullable=True),
        pa.field("news_articles_found", pa.int32(), nullable=True),
        pa.field("revision", pa.int32(), nullable=False),
        pa.field("is_flip", pa.bool_(), nullable=False),
        pa.field("written_at", pa.string(), nullable=False),
        pa.field("event_date", pa.string(), nullable=True),
    ]
)

_ICEBERG_SCHEMA = Schema(
    NestedField(1, "alert_id", StringType(), required=True),
    NestedField(2, "symbol", StringType(), required=True),
    NestedField(3, "event_ts", StringType(), required=True),
    NestedField(4, "rule_name", StringType(), required=True),
    NestedField(5, "severity", StringType(), required=False),
    NestedField(6, "judgement", StringType(), required=True),
    NestedField(7, "news_category", StringType(), required=False),
    NestedField(8, "llm_explanation", StringType(), required=False),
    NestedField(9, "news_articles_found", IntegerType(), required=False),
    NestedField(10, "revision", IntegerType(), required=True),
    NestedField(11, "is_flip", BooleanType(), required=True),
    NestedField(12, "written_at", StringType(), required=True),
    NestedField(13, "event_date", StringType(), required=False),
)

_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=13,
        field_id=1000,
        name="event_date",
        transform=IdentityTransform(),
    )
)

_TABLE_PROPERTIES: dict[str, str] = {
    "write.format.default": "parquet",
    "write.parquet.compression-codec": "zstd",
}


class JudgementWriter(BaseIcebergWriter):
    """Appends LLM judgement rows to ``gold.anomaly_judgement`` (opt-in).

    Raises:
        TimeoutError: Commit exceeded ``judgement_write_timeout_sec``. Do NOT
            DLQ on this error — the background thread may still commit.
    """

    def __init__(self) -> None:
        super().__init__(thread_name_prefix="judgement-writer")
        self._enabled = False

    def init(self, cfg: Settings) -> None:
        """Ensure-create ``gold.anomaly_judgement`` and init the write executor.

        No-op when ``delivery_source != CONFIRMED`` — the table is irrelevant
        in raw mode and should not be created (avoids spurious catalog noise).

        Safe to call multiple times: reloads the catalog each call, draining
        the previous executor first (crash-loop restart picks up fresh
        credentials without risking a duplicate-append race).
        """
        if cfg.delivery_source != DeliverySource.CONFIRMED:
            logger.info("judgement_writer_skipped", delivery_source=cfg.delivery_source.value)
            self._enabled = False
            return

        self._enabled = True
        iceberg = cfg.iceberg
        catalog = load_catalog(iceberg.catalog_name, **catalog_kwargs_from_settings(iceberg))

        # Derive namespace from table identifier, e.g. "gold.anomaly_judgement" → ("gold",)
        namespace = tuple(iceberg.anomaly_judgement_table.split(".")[:-1])
        try:
            catalog.create_namespace(namespace)
            logger.info("judgement_namespace_created", namespace=list(namespace))
        except NamespaceAlreadyExistsError:
            pass

        try:
            catalog.create_table(
                identifier=iceberg.anomaly_judgement_table,
                schema=_ICEBERG_SCHEMA,
                partition_spec=_PARTITION_SPEC,
                properties=_TABLE_PROPERTIES,
            )
            logger.info("judgement_table_created", table=iceberg.anomaly_judgement_table)
        except TableAlreadyExistsError:
            pass

        self._finish_open(catalog, iceberg.anomaly_judgement_table, drain_previous=True)
        logger.info("judgement_writer_initialized", table=iceberg.anomaly_judgement_table)

    @property
    def enabled(self) -> bool:
        """Whether ``init()`` actually opened the table (``delivery_source == CONFIRMED``)."""
        return self._enabled

    async def append_initial(self, event: ConfirmedAlertEvent, cfg: Settings) -> None:
        """Append revision=0 row for a newly classified ConfirmedAlertEvent.

        No-op in raw mode (``delivery_source != CONFIRMED``).
        """
        if cfg.delivery_source != DeliverySource.CONFIRMED:
            return

        now = datetime.now(timezone.utc).isoformat()
        event_date = event.event_ts[:10] if event.event_ts else None

        arrow = pa.table(
            {
                "alert_id": [event.alert_id],
                "symbol": [event.symbol],
                "event_ts": [event.event_ts],
                "rule_name": [event.rule_name.value],
                "severity": [event.severity.value],
                "judgement": [event.llm_judgement.value],
                "news_category": [event.news_category.value if event.news_category else None],
                "llm_explanation": [event.final_explanation],
                "news_articles_found": pa.array([len(event.news_refs)], type=pa.int32()),
                "revision": pa.array([0], type=pa.int32()),
                "is_flip": [False],
                "written_at": [now],
                "event_date": [event_date],
            },
            schema=_ARROW_SCHEMA,
        )

        await self._write(
            arrow,
            timeout=cfg.iceberg.judgement_write_timeout_sec,
            timeout_event="judgement_write_timeout_unknown_state",
            failure_event="judgement_write_failed",
            alert_id=event.alert_id,
        )
        logger.info(
            "judgement_initial_appended",
            alert_id=event.alert_id,
            symbol=event.symbol,
            judgement=event.llm_judgement.value,
        )

    async def append_followup(self, event: FollowUpEvent, cfg: Settings) -> None:
        """Append revision=1 row for a FollowUpEvent re-check result.

        revision is always 1 because the current pipeline schedules exactly
        one re-check per alert (bounded by RECHECK_DELAY_MIN). ``is_flip`` is
        True when ``new_judgement`` differs from ``prev_judgement``.
        No-op in raw mode.
        """
        if cfg.delivery_source != DeliverySource.CONFIRMED:
            return

        is_flip = event.prev_judgement != event.new_judgement
        now = datetime.now(timezone.utc).isoformat()
        # Use original alert event_ts when populated by llm-agent (Stage D).
        # Fall back to emitted_at so the column is never NULL.
        event_ts = event.event_ts if event.event_ts else event.emitted_at
        event_date = event_ts[:10] if event_ts else None
        rule_name = event.rule_name or ""

        arrow = pa.table(
            {
                "alert_id": [event.ref_alert_id],
                "symbol": [event.symbol],
                "event_ts": [event_ts],
                "rule_name": [rule_name],
                "severity": pa.array([None], type=pa.string()),
                "judgement": [event.new_judgement.value],
                "news_category": pa.array([None], type=pa.string()),
                "llm_explanation": [event.news_summary],
                "news_articles_found": pa.array([len(event.news_refs)], type=pa.int32()),
                "revision": pa.array([1], type=pa.int32()),
                "is_flip": [is_flip],
                "written_at": [now],
                "event_date": [event_date],
            },
            schema=_ARROW_SCHEMA,
        )

        await self._write(
            arrow,
            timeout=cfg.iceberg.judgement_write_timeout_sec,
            timeout_event="judgement_write_timeout_unknown_state",
            failure_event="judgement_write_failed",
            alert_id=event.ref_alert_id,
        )
        logger.info(
            "judgement_followup_appended",
            alert_id=event.ref_alert_id,
            symbol=event.symbol,
            is_flip=is_flip,
            new_judgement=event.new_judgement.value,
        )
