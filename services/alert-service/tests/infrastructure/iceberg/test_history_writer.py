"""Tests for HistoryWriter — gold.fact_alert_history append-only writer.

Phase 1: no prior test file existed for this writer (it previously lived as
untested module-global functions). Catalog/table/executor now live on the
instance, so each test constructs its own writer instead of patching module
globals.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from alert_service.core.config import Settings
from alert_service.core.schema import AlertEvent, AlertSeverity, RuleName
from alert_service.infrastructure.iceberg.history_writer import _SCHEMA, HistoryWriter

_MODULE = "alert_service.infrastructure.iceberg.history_writer"

def _cfg() -> Settings:
    return Settings(telegram_bot_token="tok", telegram_chat_id=1)

def _event() -> AlertEvent:
    return AlertEvent(
        alert_id="hw-test-001",
        symbol="AAPL",
        event_ts="2026-06-01T14:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=4.8,
        threshold=3.0,
        context_snapshot={"z_price": 4.8},
    )

def _mock_catalog() -> MagicMock:
    catalog = MagicMock()
    catalog.load_table.return_value = MagicMock()
    return catalog

def _initialized_writer(catalog: MagicMock | None = None) -> HistoryWriter:
    writer = HistoryWriter()
    with patch(f"{_MODULE}.load_catalog", return_value=catalog or _mock_catalog()):
        writer.init(_cfg())
    return writer

class TestInitHistoryWriter:
    def test_loads_catalog_and_table(self) -> None:
        catalog = _mock_catalog()
        writer = _initialized_writer(catalog)
        catalog.load_table.assert_called_once_with("gold.fact_alert_history")
        writer.close()

    def test_reinit_does_not_wait_for_previous_executor(self) -> None:
        """History writer restarts fast (drain_previous=False) — unlike judgement writer."""
        writer = _initialized_writer()
        first_executor = writer._executor
        with patch(f"{_MODULE}.load_catalog", return_value=_mock_catalog()):
            writer.init(_cfg())
        assert writer._executor is not first_executor
        writer.close()

class TestAppendBatch:
    async def test_writes_one_row_per_recipient(self) -> None:
        captured = []
        writer = _initialized_writer()
        with patch.object(writer, "_append_blocking", side_effect=captured.append):
            await writer.append_batch(_event(), user_ids=["u1", "u2", None])
        writer.close()

        row = captured[0].to_pydict()
        assert row["user_id"] == ["u1", "u2", None]
        assert row["alert_id"] == ["hw-test-001"] * 3

    async def test_schema_matches(self) -> None:
        captured = []
        writer = _initialized_writer()
        with patch.object(writer, "_append_blocking", side_effect=captured.append):
            await writer.append_batch(_event(), user_ids=[None])
        writer.close()
        assert captured[0].schema == _SCHEMA

    async def test_timeout_propagates_and_does_not_write(self) -> None:
        writer = _initialized_writer()
        with patch.object(
            writer, "_append_blocking", side_effect=lambda arrow: time.sleep(0.05)
        ):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    writer.append_batch(_event(), user_ids=[None]), timeout=0.001
                )
        writer.close()

    async def test_append_is_single_recipient_wrapper(self) -> None:
        captured = []
        writer = _initialized_writer()
        with patch.object(writer, "_append_blocking", side_effect=captured.append):
            await writer.append(_event(), user_id="u1")
        writer.close()
        assert captured[0].to_pydict()["user_id"] == ["u1"]

    async def test_uninitialized_writer_raises(self) -> None:
        writer = HistoryWriter()
        with pytest.raises(RuntimeError):
            await writer.append_batch(_event(), user_ids=[None])

class TestCloseHistoryWriter:
    def test_noop_if_never_initialized(self) -> None:
        HistoryWriter().close()  # must not raise

    def test_shuts_executor(self) -> None:
        writer = HistoryWriter()
        mock_exec = MagicMock()
        writer._executor = mock_exec
        writer.close()
        mock_exec.shutdown.assert_called_once_with(wait=True)
        assert writer._executor is None
