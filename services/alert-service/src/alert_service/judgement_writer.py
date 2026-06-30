"""Iceberg writer for gold.anomaly_judgement (Stage D — OPT-IN analytics).

Writes LLM judgement records ONLY when DELIVERY_SOURCE=confirmed.
Append-only event log — never updates or deletes existing rows.

Design mirrors history_writer.py:
  - Module-level singletons initialised once at startup (init_judgement_writer).
  - Single-worker ThreadPoolExecutor serialises all Iceberg commits (Bug #2 fix).
  - TimeoutError from asyncio.wait_for must NOT trigger DLQ replay — the
    background thread may still complete, and a replay would produce a duplicate
    row in the append-only table (Bug #3 pattern).

On service start with DELIVERY_SOURCE=raw: init is a no-op; the table is never
created, existing rule-based flow is untouched.

Schema (gold.anomaly_judgement):
  alert_id, symbol, event_ts, rule_name, severity, judgement, news_category,
  llm_explanation, news_articles_found, revision, is_flip, written_at, event_date
See §11.5 of ai-agent-plan.md for the full DDL and dashboard queries.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
from datetime import datetime, timezone

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, TableAlreadyExistsError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    BooleanType,
    IntegerType,
    NestedField,
    StringType,
)

from alert_service.config import DeliverySource, Settings
from alert_service.schema import ConfirmedAlertEvent, FollowUpEvent

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

# Module-level singletons — populated by init_judgement_writer().
_catalog: Catalog | None = None
_table: Table | None = None
_cfg_ref: Settings | None = None
_write_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _catalog_kwargs(cfg: Settings) -> dict[str, str]:
    """Build PyIceberg REST catalog kwargs from alert-service Settings."""
    return {
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
    }


def init_judgement_writer(cfg: Settings) -> None:
    """Ensure-create gold.anomaly_judgement and init the write executor.

    No-op when delivery_source != CONFIRMED — the table is irrelevant in raw mode
    and should not be created (avoids spurious catalog noise).

    Safe to call multiple times: reloads catalog on each call, replacing the
    previous executor (crash-loop restart picks up fresh credentials).
    """
    global _catalog, _table, _cfg_ref, _write_executor

    if cfg.delivery_source != DeliverySource.CONFIRMED:
        logger.info(
            "judgement_writer_skipped", delivery_source=cfg.delivery_source.value
        )
        return

    _cfg_ref = cfg
    _catalog = load_catalog(cfg.iceberg_catalog_name, **_catalog_kwargs(cfg))

    # Derive namespace from table identifier, e.g. "gold.anomaly_judgement" → ("gold",)
    table_parts = cfg.anomaly_judgement_table.split(".")
    namespace = tuple(table_parts[:-1])

    try:
        _catalog.create_namespace(namespace)
        logger.info("judgement_namespace_created", namespace=list(namespace))
    except NamespaceAlreadyExistsError:
        pass

    try:
        _catalog.create_table(
            identifier=cfg.anomaly_judgement_table,
            schema=_ICEBERG_SCHEMA,
            partition_spec=_PARTITION_SPEC,
            properties=_TABLE_PROPERTIES,
        )
        logger.info("judgement_table_created", table=cfg.anomaly_judgement_table)
    except TableAlreadyExistsError:
        pass

    _table = _catalog.load_table(cfg.anomaly_judgement_table)

    if _write_executor is not None:
        # wait=True: drain any in-flight commit before replacing the executor.
        # wait=False would allow old and new threads to call _table.append()
        # concurrently, risking duplicate rows in the append-only table.
        _write_executor.shutdown(wait=True)
    _write_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="judgement-writer"
    )
    logger.info("judgement_writer_initialized", table=cfg.anomaly_judgement_table)


def close_judgement_writer() -> None:
    """Drain the write executor on service shutdown.

    Blocks until any in-flight Iceberg commit completes.
    Safe to call if init_judgement_writer() was never called.
    """
    global _write_executor
    if _write_executor is not None:
        _write_executor.shutdown(wait=True)
        _write_executor = None
        logger.info("judgement_writer_closed")


def _reload_table() -> Table:
    """Reload the table handle from the cached catalog (no auth round-trip)."""
    if _catalog is None or _cfg_ref is None:
        raise RuntimeError(
            "init_judgement_writer() must be called during startup before any writes"
        )
    global _table
    _table = _catalog.load_table(_cfg_ref.anomaly_judgement_table)
    return _table


def _append_blocking(arrow: pa.Table) -> None:
    """Blocking Iceberg append — runs exclusively in the single-worker executor."""
    if _table is None:
        raise RuntimeError(
            "init_judgement_writer() must be called during startup before any writes"
        )
    try:
        _table.append(arrow)
    except Exception as exc:
        # Stale table handle (e.g., catalog server restart) — reload once and retry.
        logger.warning("judgement_append_stale_handle_retry", error=str(exc))
        fresh = _reload_table()
        fresh.append(arrow)


async def _submit_write(arrow: pa.Table, alert_id: str, timeout: float) -> None:
    if _write_executor is None:
        raise RuntimeError(
            "init_judgement_writer() must be called during startup before any writes"
        )
    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                _write_executor, functools.partial(_append_blocking, arrow)
            ),
            timeout=timeout,
        )
    except TimeoutError:
        logger.error(
            "judgement_write_timeout_unknown_state",
            alert_id=alert_id,
            timeout_sec=timeout,
        )
        raise
    except Exception as exc:
        logger.error("judgement_write_failed", alert_id=alert_id, error=str(exc))
        raise


async def append_initial_judgement(
    event: ConfirmedAlertEvent,
    cfg: Settings,
) -> None:
    """Append revision=0 row for a newly classified ConfirmedAlertEvent.

    Called by the confirmed-alert handler when DELIVERY_SOURCE=confirmed.
    No-op in raw mode.

    Args:
        event: The confirmed alert with LLM judgement.
        cfg:   Service settings (reads delivery_source, timeout).

    Raises:
        TimeoutError: Commit exceeded judgement_write_timeout_sec.
            Do NOT DLQ on this error — the background thread may still commit.
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
            "news_category": [
                event.news_category.value if event.news_category else None
            ],
            "llm_explanation": [event.final_explanation],
            "news_articles_found": pa.array([len(event.news_refs)], type=pa.int32()),
            "revision": pa.array([0], type=pa.int32()),
            "is_flip": [False],
            "written_at": [now],
            "event_date": [event_date],
        },
        schema=_ARROW_SCHEMA,
    )

    await _submit_write(arrow, event.alert_id, cfg.judgement_write_timeout_sec)
    logger.info(
        "judgement_initial_appended",
        alert_id=event.alert_id,
        symbol=event.symbol,
        judgement=event.llm_judgement.value,
    )


async def append_followup_judgement(
    event: FollowUpEvent,
    cfg: Settings,
) -> None:
    """Append revision=1 row for a FollowUpEvent re-check result.

    revision is always 1 because the current pipeline schedules exactly one
    re-check per alert (bounded by RECHECK_DELAY_MIN).

    is_flip=True when new_judgement differs from prev_judgement (UNEXPLAINED→EXPLAINED).
    is_flip=False when new_judgement == prev_judgement (UNEXPLAINED confirmed).

    Args:
        event: The follow-up re-check result.
        cfg:   Service settings (reads delivery_source, timeout).

    Raises:
        TimeoutError: Commit exceeded judgement_write_timeout_sec.
            Do NOT DLQ on this error — the background thread may still commit.
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

    await _submit_write(arrow, event.ref_alert_id, cfg.judgement_write_timeout_sec)
    logger.info(
        "judgement_followup_appended",
        alert_id=event.ref_alert_id,
        symbol=event.symbol,
        is_flip=is_flip,
        new_judgement=event.new_judgement.value,
    )
