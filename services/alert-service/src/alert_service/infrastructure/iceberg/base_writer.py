"""Shared instance-based Iceberg append-only writer.

Phase 1 refactor: ``history_writer.py`` and ``judgement_writer.py`` used to keep
their catalog/table/executor as **module-level globals**, which made every
writer a hidden singleton — untestable in isolation and impossible to run two
independent instances (e.g. one real + one fake in a test) side by side.

This base class moves that state onto the instance. Subclasses own the
catalog-specific bootstrapping (namespace/table creation, schema) and call
:meth:`_finish_open` once they have a ``Catalog`` and a table identifier.

All writes for a given instance are serialised through a single-worker
``ThreadPoolExecutor`` so there is never more than one concurrent Iceberg
commit in flight for that table (avoids the ``CommitFailedException`` race
from concurrent ``asyncio.to_thread`` appends on the same ``Table`` object).

``asyncio.wait_for`` cannot cancel a running thread, so on timeout the commit
outcome is *unknown* — the background thread may still complete. Callers
**must not** treat a ``TimeoutError`` from :meth:`_write` as a delivery
failure to retry/DLQ, since replay would produce a duplicate row in the
append-only table.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog
from pyiceberg.table import Table

from alert_service.core.config import IcebergSettings

logger = structlog.get_logger(__name__)


class BaseIcebergWriter:
    """Owns one Iceberg table handle plus its single-worker write executor."""

    def __init__(self, *, thread_name_prefix: str) -> None:
        self._thread_name_prefix = thread_name_prefix
        self._catalog: Catalog | None = None
        self._table: Table | None = None
        self._table_identifier: str | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def _finish_open(
        self, catalog: Catalog, table_identifier: str, *, drain_previous: bool
    ) -> None:
        """Load the table handle and (re)create the write executor.

        Args:
            catalog: An already-authenticated catalog handle.
            table_identifier: Fully qualified table name, e.g. ``"gold.fact_alert_history"``.
            drain_previous: When ``True``, block until any in-flight commit on
                the previous executor completes before replacing it (required
                whenever two executors could otherwise append concurrently to
                the same append-only table). When ``False``, replace
                immediately without waiting.
        """
        self._catalog = catalog
        self._table_identifier = table_identifier
        self._table = catalog.load_table(table_identifier)
        if self._executor is not None:
            self._executor.shutdown(wait=drain_previous)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=self._thread_name_prefix
        )

    def close(self) -> None:
        """Drain the write executor gracefully. Safe to call if never opened."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def _reload_table(self) -> Table:
        """Reload the table handle from the cached catalog (no auth round-trip)."""
        if self._catalog is None or self._table_identifier is None:
            raise RuntimeError(f"{type(self).__name__}.init() must be called before any writes")
        self._table = self._catalog.load_table(self._table_identifier)
        return self._table

    def _append_blocking(self, arrow: pa.Table) -> None:
        """Blocking Iceberg append — runs exclusively in the single-worker executor."""
        if self._table is None:
            raise RuntimeError(f"{type(self).__name__}.init() must be called before any writes")
        try:
            self._table.append(arrow)
        except Exception:
            # Stale table handle (e.g., catalog server restart) — reload once and retry.
            fresh = self._reload_table()
            fresh.append(arrow)

    async def _write(
        self,
        arrow: pa.Table,
        *,
        timeout: float,
        timeout_event: str,
        failure_event: str,
        **log_context: object,
    ) -> None:
        """Submit ``arrow`` to the single-worker executor and await it with a timeout.

        Raises:
            TimeoutError: Commit exceeded ``timeout``. Do not DLQ/replay on this —
                the background thread may still commit the row.
            RuntimeError: The writer was never opened.
            Exception: Any Iceberg / S3 error after the stale-handle retry.
        """
        if self._executor is None:
            raise RuntimeError(f"{type(self).__name__}.init() must be called before any writes")
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(self._executor, functools.partial(self._append_blocking, arrow)),
                timeout=timeout,
            )
        except TimeoutError:
            logger.error(timeout_event, timeout_sec=timeout, **log_context)
            raise
        except Exception as exc:
            logger.error(failure_event, error=str(exc), **log_context)
            raise


def catalog_kwargs_from_settings(iceberg: IcebergSettings) -> dict[str, str]:
    """Build the PyIceberg REST catalog kwargs shared by every writer."""
    return {
        "type": "rest",
        "uri": iceberg.catalog_uri,
        "rest.auth.type": "oauth2",
        "oauth2-server-uri": iceberg.oauth2_server_uri,
        "credential": iceberg.oauth2_credential,
        "scope": iceberg.oauth2_scope,
        "token-exchange-enabled": "false",
        "warehouse": iceberg.warehouse,
        "header.X-Iceberg-Access-Delegation": "",
        "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
        "s3.endpoint": iceberg.s3_endpoint,
        "s3.access-key-id": iceberg.s3_access_key_id,
        "s3.secret-access-key": iceberg.s3_secret_access_key,
        "s3.region": iceberg.s3_region,
        "s3.path-style-access": str(iceberg.s3_path_style_access).lower(),
    }
