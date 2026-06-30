"""Tests for judgement_writer — OPT-IN anomaly_judgement append-only writer.

All Iceberg catalog operations are mocked — no live catalog needed.
Verifies:
  - init is a no-op in RAW mode
  - init ensures-create namespace + table in CONFIRMED mode
  - TableAlreadyExistsError / NamespaceAlreadyExistsError handled gracefully
  - append_initial_judgement builds correct revision=0 row
  - append_followup_judgement detects is_flip correctly
  - append functions are no-ops in RAW mode
  - Bước 9 invariant: 1 alert + 1 follow-up → 2 rows with same alert_id
  - close_judgement_writer shuts executor
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from alert_service.config import DeliverySource, Settings
from alert_service.judgement_writer import (
    _ARROW_SCHEMA,
    append_followup_judgement,
    append_initial_judgement,
    close_judgement_writer,
    init_judgement_writer,
)
from alert_service.schema import (
    AlertSeverity,
    ConfirmedAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    NewsCategory,
    RuleName,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _cfg(delivery_source: DeliverySource = DeliverySource.CONFIRMED) -> Settings:
    return Settings(
        telegram_bot_token="tok",
        telegram_chat_id=1,
        delivery_source=delivery_source,
    )


def _confirmed(
    judgement: LLMJudgement = LLMJudgement.EXPLAINED,
    category: NewsCategory | None = NewsCategory.EARNINGS,
) -> ConfirmedAlertEvent:
    return ConfirmedAlertEvent(
        alert_id="jw-test-001",
        symbol="AAPL",
        event_ts="2026-06-01T14:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=4.8,
        threshold=3.0,
        context_snapshot={"z_price": 4.8},
        llm_judgement=judgement,
        final_explanation="Strong earnings" if judgement == LLMJudgement.EXPLAINED else None,
        news_category=category,
    )


def _followup(
    prev: LLMJudgement = LLMJudgement.UNEXPLAINED,
    new: LLMJudgement = LLMJudgement.EXPLAINED,
    with_analytics: bool = True,
) -> FollowUpEvent:
    return FollowUpEvent(
        ref_alert_id="jw-test-001",
        symbol="AAPL",
        prev_judgement=prev,
        new_judgement=new,
        emitted_at="2026-06-01T14:20:00Z",
        event_ts="2026-06-01T14:00:00Z" if with_analytics else None,
        rule_name="price_zscore" if with_analytics else None,
    )


def _mock_catalog() -> MagicMock:
    catalog = MagicMock()
    catalog.load_table.return_value = MagicMock()
    return catalog


# ── init_judgement_writer ─────────────────────────────────────────────────────


class TestInitJudgementWriter:
    def test_raw_mode_is_noop(self) -> None:
        with patch("alert_service.judgement_writer.load_catalog") as mock_load:
            init_judgement_writer(_cfg(DeliverySource.RAW))
            mock_load.assert_not_called()

    def test_confirmed_mode_loads_catalog(self) -> None:
        with patch("alert_service.judgement_writer.load_catalog") as mock_load:
            mock_load.return_value = _mock_catalog()
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
            mock_load.assert_called_once()
        close_judgement_writer()

    def test_ensure_create_namespace(self) -> None:
        catalog = _mock_catalog()
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
        catalog.create_namespace.assert_called_once_with(("gold",))
        close_judgement_writer()

    def test_namespace_already_exists_is_silent(self) -> None:
        from pyiceberg.exceptions import NamespaceAlreadyExistsError

        catalog = _mock_catalog()
        catalog.create_namespace.side_effect = NamespaceAlreadyExistsError("gold")
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))  # must not raise
        close_judgement_writer()

    def test_ensure_create_table(self) -> None:
        catalog = _mock_catalog()
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
        catalog.create_table.assert_called_once()
        close_judgement_writer()

    def test_table_already_exists_is_silent(self) -> None:
        from pyiceberg.exceptions import TableAlreadyExistsError

        catalog = _mock_catalog()
        catalog.create_table.side_effect = TableAlreadyExistsError(
            "gold.anomaly_judgement"
        )
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))  # must not raise
        close_judgement_writer()


# ── append_initial_judgement ──────────────────────────────────────────────────


class TestAppendInitialJudgement:
    async def test_noop_in_raw_mode(self) -> None:
        captured: list[pa.Table] = []
        with patch("alert_service.judgement_writer._append_blocking", side_effect=captured.append):
            await append_initial_judgement(_confirmed(), _cfg(DeliverySource.RAW))
        assert captured == []

    async def _run_append(self, event: ConfirmedAlertEvent) -> dict[str, list]:
        captured: list[pa.Table] = []
        catalog = _mock_catalog()
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
        with patch("alert_service.judgement_writer._append_blocking", side_effect=captured.append):
            await append_initial_judgement(event, _cfg(DeliverySource.CONFIRMED))
        close_judgement_writer()
        return captured[0].to_pydict()

    async def test_revision_is_0(self) -> None:
        row = await self._run_append(_confirmed())
        assert row["revision"] == [0]

    async def test_is_flip_is_false(self) -> None:
        row = await self._run_append(_confirmed())
        assert row["is_flip"] == [False]

    async def test_alert_id_preserved(self) -> None:
        row = await self._run_append(_confirmed())
        assert row["alert_id"] == ["jw-test-001"]

    async def test_judgement_value(self) -> None:
        row = await self._run_append(_confirmed(LLMJudgement.UNEXPLAINED, None))
        assert row["judgement"] == ["UNEXPLAINED"]

    async def test_news_category_set(self) -> None:
        row = await self._run_append(_confirmed(LLMJudgement.EXPLAINED, NewsCategory.MACRO))
        assert row["news_category"] == ["MACRO"]

    async def test_news_category_null_when_none(self) -> None:
        row = await self._run_append(_confirmed(LLMJudgement.UNEXPLAINED, None))
        assert row["news_category"] == [None]

    async def test_event_date_extracted_from_event_ts(self) -> None:
        row = await self._run_append(_confirmed())
        assert row["event_date"] == ["2026-06-01"]

    async def test_schema_matches_arrow_schema(self) -> None:
        captured: list[pa.Table] = []
        catalog = _mock_catalog()
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
        with patch("alert_service.judgement_writer._append_blocking", side_effect=captured.append):
            await append_initial_judgement(_confirmed(), _cfg(DeliverySource.CONFIRMED))
        close_judgement_writer()
        assert captured[0].schema == _ARROW_SCHEMA


# ── append_followup_judgement ─────────────────────────────────────────────────


class TestAppendFollowupJudgement:
    async def test_noop_in_raw_mode(self) -> None:
        captured: list[pa.Table] = []
        with patch("alert_service.judgement_writer._append_blocking", side_effect=captured.append):
            await append_followup_judgement(_followup(), _cfg(DeliverySource.RAW))
        assert captured == []

    async def _run_append(self, event: FollowUpEvent) -> dict[str, list]:
        captured: list[pa.Table] = []
        catalog = _mock_catalog()
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
        with patch("alert_service.judgement_writer._append_blocking", side_effect=captured.append):
            await append_followup_judgement(event, _cfg(DeliverySource.CONFIRMED))
        close_judgement_writer()
        return captured[0].to_pydict()

    async def test_revision_is_1(self) -> None:
        row = await self._run_append(_followup())
        assert row["revision"] == [1]

    async def test_flip_sets_is_flip_true(self) -> None:
        row = await self._run_append(
            _followup(LLMJudgement.UNEXPLAINED, LLMJudgement.EXPLAINED)
        )
        assert row["is_flip"] == [True]

    async def test_confirm_sets_is_flip_false(self) -> None:
        row = await self._run_append(
            _followup(LLMJudgement.UNEXPLAINED, LLMJudgement.UNEXPLAINED)
        )
        assert row["is_flip"] == [False]

    async def test_new_judgement_in_row(self) -> None:
        row = await self._run_append(
            _followup(LLMJudgement.UNEXPLAINED, LLMJudgement.EXPLAINED)
        )
        assert row["judgement"] == ["EXPLAINED"]

    async def test_uses_original_event_ts(self) -> None:
        row = await self._run_append(_followup(with_analytics=True))
        assert row["event_ts"] == ["2026-06-01T14:00:00Z"]
        assert row["event_date"] == ["2026-06-01"]

    async def test_falls_back_to_emitted_at(self) -> None:
        row = await self._run_append(_followup(with_analytics=False))
        assert row["event_ts"] == ["2026-06-01T14:20:00Z"]

    async def test_two_rows_same_alert_id(self) -> None:
        """Bước 9 invariant: 1 initial + 1 followup = 2 rows, same alert_id."""
        captured: list[pa.Table] = []
        catalog = _mock_catalog()
        with patch("alert_service.judgement_writer.load_catalog", return_value=catalog):
            init_judgement_writer(_cfg(DeliverySource.CONFIRMED))
        with patch(
            "alert_service.judgement_writer._append_blocking",
            side_effect=captured.append,
        ):
            await append_initial_judgement(
                _confirmed(LLMJudgement.UNEXPLAINED, None), _cfg(DeliverySource.CONFIRMED)
            )
            await append_followup_judgement(_followup(), _cfg(DeliverySource.CONFIRMED))
        close_judgement_writer()

        assert len(captured) == 2
        row0 = captured[0].to_pydict()
        row1 = captured[1].to_pydict()
        assert row0["revision"] == [0]
        assert row1["revision"] == [1]
        assert row0["alert_id"] == row1["alert_id"]


# ── close_judgement_writer ────────────────────────────────────────────────────


class TestCloseJudgementWriter:
    def test_noop_if_never_initialized(self) -> None:
        import alert_service.judgement_writer as jw

        jw._write_executor = None
        close_judgement_writer()  # must not raise

    def test_shuts_executor(self) -> None:
        import alert_service.judgement_writer as jw

        mock_exec = MagicMock()
        jw._write_executor = mock_exec
        close_judgement_writer()
        mock_exec.shutdown.assert_called_once_with(wait=True)
        assert jw._write_executor is None
