"""Tests for user_alert_processor.py — UserAlertProcessor."""
import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from rule_engine.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from rule_engine.domain.models import UserAlertRule
from rule_engine.infrastructure.db.repository import UserAlertRepository
from rule_engine.domain.schema import QuoteEvent
from rule_engine.application.user_alert_processor import UserAlertProcessor


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_rule(
    symbols: list[str] | None = None,
    field: AlertField = AlertField.PRICE,
    operator: AlertOperator = AlertOperator.GT,
    threshold: float = 100.0,
    frequency: AlertFrequency = AlertFrequency.EVERY_TIME,
    cooldown_min: int = 0,
) -> UserAlertRule:
    return UserAlertRule(
        rule_id=uuid4(),
        user_id=uuid4(),
        symbols=symbols if symbols is not None else ["AAPL"],
        field=field,
        operator=operator,
        threshold=threshold,
        frequency=frequency,
        cooldown_min=cooldown_min,
        status=AlertStatus.ACTIVE,
    )


def _make_quote(symbol: str = "AAPL", price: float = 150.0) -> QuoteEvent:
    return QuoteEvent(
        symbol=symbol,
        price=price,
        change_pct=0.0,
        day_volume=1_000_000,
        day_high=155.0,
        day_low=145.0,
        prev_close=140.0,
        event_ts="2026-05-18T10:00:00Z",
    )


def _make_mock_repo(rules: list[UserAlertRule] | None = None) -> AsyncMock:
    repo = AsyncMock(spec=UserAlertRepository)
    repo.get_active_rules.return_value = rules or []
    repo.insert_event.return_value = None
    repo.mark_triggered.return_value = None
    return repo


# ── reload_rules ──────────────────────────────────────────────────────────────


class TestReloadRules:
    @pytest.mark.asyncio
    async def test_returns_count_of_loaded_rules(self) -> None:
        repo = _make_mock_repo([_make_rule(), _make_rule()])
        processor = UserAlertProcessor(repo)

        count = await processor.reload_rules()

        assert count == 2

    @pytest.mark.asyncio
    async def test_updates_rules_cache(self) -> None:
        rule = _make_rule()
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)

        await processor.reload_rules()

        assert len(processor._rules_cache) == 1
        assert processor._rules_cache[0] is rule

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_active_rules(self) -> None:
        repo = _make_mock_repo([])
        processor = UserAlertProcessor(repo)

        count = await processor.reload_rules()

        assert count == 0


# ── evaluate ──────────────────────────────────────────────────────────────────


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_skips_rule_when_symbol_not_in_rule_symbols(self) -> None:
        rule = _make_rule(symbols=["MSFT"])
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL"), None, publisher)

        publisher.publish.assert_not_awaited()
        repo.insert_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fires_for_wildcard_symbol_rule(self) -> None:
        rule = _make_rule(symbols=["*"], threshold=100.0)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("TSLA", 150.0), None, publisher)

        repo.insert_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_condition_not_met(self) -> None:
        rule = _make_rule(threshold=200.0, operator=AlertOperator.GT)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.insert_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inserts_event_when_condition_met(self) -> None:
        rule = _make_rule(threshold=100.0, operator=AlertOperator.GT)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.insert_event.assert_awaited_once()
        event = repo.insert_event.call_args[0][0]
        assert event.symbol == "AAPL"
        assert event.triggered_value == 150.0

    @pytest.mark.asyncio
    async def test_publishes_kafka_event_when_condition_met(self) -> None:
        rule = _make_rule(threshold=100.0)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        publisher.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_rule_with_null_rule_id(self) -> None:
        rule = UserAlertRule(
            rule_id=None,
            user_id=uuid4(),
            symbols=["AAPL"],
            field=AlertField.PRICE,
            operator=AlertOperator.GT,
            threshold=100.0,
        )
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.insert_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_second_fire_within_cooldown(self) -> None:
        rule = _make_rule(cooldown_min=60, threshold=100.0)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)
        repo.insert_event.reset_mock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.insert_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_marks_once_rule_as_triggered(self) -> None:
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.ONCE)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.mark_triggered.assert_awaited_once_with(rule.rule_id)

    @pytest.mark.asyncio
    async def test_removes_once_rule_from_cache_after_trigger(self) -> None:
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.ONCE)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        assert len(processor._rules_cache) == 0

    @pytest.mark.asyncio
    async def test_keeps_rule_in_cache_when_db_mark_triggered_fails(self) -> None:
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.ONCE)
        repo = _make_mock_repo([rule])
        repo.mark_triggered.side_effect = asyncpg.PostgresError("DB down")
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        assert len(processor._rules_cache) == 1

    @pytest.mark.asyncio
    async def test_every_time_rule_never_calls_mark_triggered(self) -> None:
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.EVERY_TIME)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.mark_triggered.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mark_triggered_db_error_is_swallowed(self) -> None:
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.ONCE)
        repo = _make_mock_repo([rule])
        repo.mark_triggered.side_effect = asyncpg.PostgresError("DB down")
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        # Must not raise; failure is logged only.
        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)


# ── update_prev_values ────────────────────────────────────────────────────────


class TestUpdatePrevValues:
    @pytest.mark.asyncio
    async def test_stores_price(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())

        await processor.update_prev_values(_make_quote("AAPL", 150.0), None)

        assert processor._prev_values[("AAPL", AlertField.PRICE)] == 150.0

    @pytest.mark.asyncio
    async def test_stores_day_volume(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())
        quote = QuoteEvent(
            symbol="AAPL",
            price=100.0,
            change_pct=0.0,
            day_volume=2_000_000,
            day_high=102.0,
            day_low=99.0,
            prev_close=99.0,
            event_ts="2026-05-18T10:00:00Z",
        )

        await processor.update_prev_values(quote, None)

        assert processor._prev_values[("AAPL", AlertField.DAY_VOLUME)] == 2_000_000.0

    @pytest.mark.asyncio
    async def test_stores_context_fields_when_ctx_provided(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())
        # _make_quote has prev_close=140.0; with std_return_20d=0.01 and mean=0:
        # price_zscore = ((150 - 140) / 140) / 0.01 = 7.142...
        ctx = {
            "rsi_14": 75.0,
            "mean_return_20d": 0.0,
            "std_return_20d": 0.01,
            "mean_volume_20d": 500_000.0,
            "std_volume_20d": 200_000.0,
        }

        await processor.update_prev_values(_make_quote("AAPL"), ctx)

        assert processor._prev_values[("AAPL", AlertField.RSI_14)] == 75.0
        # Computed PRICE_ZSCORE is stored (not a context key, but computed on-the-fly)
        assert ("AAPL", AlertField.PRICE_ZSCORE) in processor._prev_values

    @pytest.mark.asyncio
    async def test_crossing_detection_uses_prev_value(self) -> None:
        rule = _make_rule(
            field=AlertField.PRICE,
            operator=AlertOperator.CROSSES_UP,
            threshold=140.0,
            cooldown_min=0,
        )
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        # Record prev value below threshold
        await processor.update_prev_values(_make_quote("AAPL", 130.0), None)

        # Next quote crosses above threshold
        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.insert_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_crossing_without_prev_value(self) -> None:
        rule = _make_rule(
            field=AlertField.PRICE,
            operator=AlertOperator.CROSSES_UP,
            threshold=140.0,
        )
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        # No previous value — CROSSES_UP must not fire
        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.insert_event.assert_not_awaited()


# ── _check_cooldown / _record_fired ──────────────────────────────────────────


class TestInCooldown:
    @pytest.mark.asyncio
    async def test_first_fire_not_in_cooldown(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())
        rule_id = uuid4()
        now = datetime.now(UTC)

        result = await processor._check_cooldown(rule_id, "AAPL", now, cooldown_min=60)

        assert result is False

    @pytest.mark.asyncio
    async def test_immediate_second_fire_in_cooldown(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())
        rule_id = uuid4()
        now = datetime.now(UTC)

        # First: check says no cooldown, then we record the fire.
        assert await processor._check_cooldown(rule_id, "AAPL", now, cooldown_min=60) is False
        await processor._record_fired(rule_id, "AAPL", now)
        result = await processor._check_cooldown(rule_id, "AAPL", now, cooldown_min=60)

        assert result is True

    @pytest.mark.asyncio
    async def test_zero_cooldown_always_allows(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())
        rule_id = uuid4()
        now = datetime.now(UTC)

        await processor._record_fired(rule_id, "AAPL", now)
        result = await processor._check_cooldown(rule_id, "AAPL", now, cooldown_min=0)

        assert result is False

    @pytest.mark.asyncio
    async def test_different_symbols_have_independent_cooldowns(self) -> None:
        processor = UserAlertProcessor(_make_mock_repo())
        rule_id = uuid4()
        now = datetime.now(UTC)

        await processor._record_fired(rule_id, "AAPL", now)
        result = await processor._check_cooldown(rule_id, "MSFT", now, cooldown_min=60)

        assert result is False
