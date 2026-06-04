"""Tests for custom alert Kafka publish path (replaces deleted test_telegram_service.py).

Verifies that UserAlertProcessor.evaluate publishes a CustomAlertEvent via the
injected publisher rather than calling Telegram directly (ADR-001 / SoC refactor).
"""
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from rule_engine.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from rule_engine.domain.models import UserAlertRule
from rule_engine.domain.schema import CustomAlertEvent, QuoteEvent
from rule_engine.application.user_alert_processor import UserAlertProcessor
from rule_engine.infrastructure.db.repository import UserAlertRepository


def _make_rule(
    symbols: list[str] | None = None,
    field: AlertField = AlertField.PRICE,
    operator: AlertOperator = AlertOperator.GT,
    threshold: float = 100.0,
    frequency: AlertFrequency = AlertFrequency.EVERY_TIME,
    cooldown_min: int = 0,
    chat_id: int | str | None = None,
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
        chat_id=chat_id,
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


class TestPublishOnFire:
    @pytest.mark.asyncio
    async def test_publishes_custom_alert_event_when_condition_met(self) -> None:
        rule = _make_rule(threshold=100.0, chat_id=1001)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        publisher.publish.assert_awaited_once()
        event = publisher.publish.call_args[0][0]
        assert isinstance(event, CustomAlertEvent)
        assert event.symbol == "AAPL"
        assert event.triggered_value == 150.0
        assert event.chat_id == 1001
        assert event.field == AlertField.PRICE.value
        assert event.operator == AlertOperator.GT.value
        assert event.threshold == 100.0

    @pytest.mark.asyncio
    async def test_does_not_publish_when_condition_not_met(self) -> None:
        rule = _make_rule(threshold=200.0, operator=AlertOperator.GT)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        publisher.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publish_carries_none_chat_id_when_not_set(self) -> None:
        rule = _make_rule(threshold=100.0, chat_id=None)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        event = publisher.publish.call_args[0][0]
        assert event.chat_id is None

    @pytest.mark.asyncio
    async def test_insert_called_before_publish(self) -> None:
        """Invariant: user_alert_events INSERT must precede Kafka publish."""
        call_order: list[str] = []

        repo = _make_mock_repo([_make_rule(threshold=100.0)])
        repo.insert_event.side_effect = lambda _: call_order.append("insert") or None

        publisher = AsyncMock()

        async def _record_publish(msg: object) -> None:
            call_order.append("publish")

        publisher.publish.side_effect = _record_publish

        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        assert call_order == ["insert", "publish"], f"Wrong order: {call_order}"

    @pytest.mark.asyncio
    async def test_no_publish_when_insert_fails(self) -> None:
        rule = _make_rule(threshold=100.0)
        repo = _make_mock_repo([rule])
        repo.insert_event.side_effect = asyncpg.PostgresError("DB down")
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        publisher.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_id_is_valid_uuid_string(self) -> None:
        import uuid

        rule = _make_rule(threshold=100.0)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        event = publisher.publish.call_args[0][0]
        uuid.UUID(event.event_id)  # raises ValueError if not a valid UUID string

    @pytest.mark.asyncio
    async def test_kafka_publish_failure_does_not_propagate(self) -> None:
        """Broker failure must be logged, not raised — prevents consumer redelivery."""
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.EVERY_TIME)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()
        publisher.publish.side_effect = RuntimeError("Kafka broker unavailable")

        # Must not raise
        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        # Insert still happened (event is in PG before publish attempt)
        repo.insert_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_once_frequency_marks_triggered_after_publish(self) -> None:
        rule = _make_rule(threshold=100.0, frequency=AlertFrequency.ONCE)
        repo = _make_mock_repo([rule])
        processor = UserAlertProcessor(repo)
        await processor.reload_rules()
        publisher = AsyncMock()

        await processor.evaluate(_make_quote("AAPL", 150.0), None, publisher)

        repo.mark_triggered.assert_awaited_once_with(rule.rule_id)
        publisher.publish.assert_awaited_once()


class TestSchemaContractRoundtrip:
    """CustomAlertEvent must have the canonical field set (Kafka contract).

    The full cross-service roundtrip (rule-engine → alert-service deserialization)
    lives in alert-service/tests/test_custom_delivery.py where alert_service is
    importable.
    """

    _EXPECTED_FIELDS = frozenset({
        "event_id", "rule_id", "user_id", "chat_id", "symbol",
        "field", "operator", "threshold", "triggered_value", "triggered_at",
    })

    def test_custom_alert_event_has_expected_fields(self) -> None:
        from rule_engine.domain.schema import CustomAlertEvent

        assert set(CustomAlertEvent.model_fields) == self._EXPECTED_FIELDS

    def test_build_produces_valid_event(self) -> None:
        from datetime import UTC, datetime
        from rule_engine.domain.schema import CustomAlertEvent

        rule = _make_rule(threshold=100.0, chat_id=1001)
        event = CustomAlertEvent.build(
            rule=rule,
            event_id="test-id",
            symbol="aapl",
            triggered_value=150.0,
            triggered_at=datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
        )
        assert event.symbol == "AAPL"
        assert event.field == "price"
        assert event.operator == ">"
        assert event.threshold == 100.0
        assert event.chat_id == 1001
