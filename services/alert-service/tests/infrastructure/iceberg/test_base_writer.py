"""Tests for ``BaseIcebergWriter`` — exercises the real stale-handle retry path.

``HistoryWriter``/``JudgementWriter`` tests patch ``_append_blocking`` directly,
which bypasses the ``except Exception: reload -> retry`` logic entirely. These
tests call the real ``_append_blocking`` / ``_reload_table`` / ``_write`` so
that safety net (recovering from a stale table handle after a catalog server
restart) is actually verified.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pyarrow as pa
import pytest

from alert_service.infrastructure.iceberg.base_writer import BaseIcebergWriter


def _writer_with_table(table: MagicMock, catalog: MagicMock | None = None) -> BaseIcebergWriter:
    writer = BaseIcebergWriter(thread_name_prefix="test-writer")
    cat = catalog or MagicMock()
    writer._finish_open(cat, "ns.table", drain_previous=False)
    writer._table = table
    cat.load_table.reset_mock()  # ignore the load_table call made by _finish_open itself
    return writer


def _arrow() -> pa.Table:
    return pa.table({"x": [1]})


class TestAppendBlocking:
    def test_first_try_succeeds_no_reload(self) -> None:
        table = MagicMock()
        catalog = MagicMock()
        writer = _writer_with_table(table, catalog)

        writer._append_blocking(_arrow())

        table.append.assert_called_once()
        catalog.load_table.assert_not_called()

    def test_stale_handle_retries_once_after_reload(self) -> None:
        stale_table = MagicMock()
        stale_table.append.side_effect = RuntimeError("stale handle")
        fresh_table = MagicMock()
        catalog = MagicMock()
        catalog.load_table.return_value = fresh_table
        writer = _writer_with_table(stale_table, catalog)

        writer._append_blocking(_arrow())

        stale_table.append.assert_called_once()
        catalog.load_table.assert_called_once_with("ns.table")
        fresh_table.append.assert_called_once()
        assert writer._table is fresh_table

    def test_retry_failure_propagates(self) -> None:
        stale_table = MagicMock()
        stale_table.append.side_effect = RuntimeError("stale handle")
        fresh_table = MagicMock()
        fresh_table.append.side_effect = RuntimeError("still broken")
        catalog = MagicMock()
        catalog.load_table.return_value = fresh_table
        writer = _writer_with_table(stale_table, catalog)

        with pytest.raises(RuntimeError, match="still broken"):
            writer._append_blocking(_arrow())

    def test_raises_if_never_opened(self) -> None:
        writer = BaseIcebergWriter(thread_name_prefix="test-writer")
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            writer._append_blocking(_arrow())


class TestReloadTable:
    def test_raises_if_never_opened(self) -> None:
        writer = BaseIcebergWriter(thread_name_prefix="test-writer")
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            writer._reload_table()


class TestWrite:
    async def test_write_before_open_raises_runtime_error(self) -> None:
        writer = BaseIcebergWriter(thread_name_prefix="test-writer")
        with pytest.raises(RuntimeError, match="init\\(\\) must be called"):
            await writer._write(_arrow(), timeout=1.0, timeout_event="t", failure_event="f")

    async def test_write_success_goes_through_real_append(self) -> None:
        table = MagicMock()
        writer = _writer_with_table(table)

        await writer._write(_arrow(), timeout=1.0, timeout_event="t", failure_event="f")

        table.append.assert_called_once()

    async def test_write_timeout_raises_timeout_error(self) -> None:
        table = MagicMock()
        table.append.side_effect = lambda arrow: time.sleep(0.05)
        writer = _writer_with_table(table)

        with pytest.raises(TimeoutError):
            await writer._write(_arrow(), timeout=0.001, timeout_event="t", failure_event="f")

    async def test_write_propagates_non_timeout_failure_after_retry(self) -> None:
        stale_table = MagicMock()
        stale_table.append.side_effect = RuntimeError("boom")
        fresh_table = MagicMock()
        fresh_table.append.side_effect = RuntimeError("boom2")
        catalog = MagicMock()
        catalog.load_table.return_value = fresh_table
        writer = _writer_with_table(stale_table, catalog)

        with pytest.raises(RuntimeError, match="boom2"):
            await writer._write(_arrow(), timeout=1.0, timeout_event="t", failure_event="f")


class TestClose:
    def test_close_noop_if_never_opened(self) -> None:
        BaseIcebergWriter(thread_name_prefix="test-writer").close()  # must not raise

    def test_close_shuts_executor(self) -> None:
        writer = BaseIcebergWriter(thread_name_prefix="test-writer")
        mock_exec = MagicMock()
        writer._executor = mock_exec
        writer.close()
        mock_exec.shutdown.assert_called_once_with(wait=True)
        assert writer._executor is None
