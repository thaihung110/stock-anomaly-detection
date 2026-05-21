"""Tests for db/client.py — DbClient methods with mocked asyncpg pool."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from rule_engine.infrastructure.db.client import DbClient
from rule_engine.domain.enums import (  # pyright: ignore[reportMissingImports]
    AlertField,
    AlertFrequency,
    AlertOperator,
    AlertStatus,
)
from rule_engine.domain.models import UserAlertEvent, UserAlertRule

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_rule_row(
    rule_id: UUID,
    user_id: UUID,
    symbols: list[str],
    field: AlertField = AlertField.PRICE,
    operator: AlertOperator = AlertOperator.GT,
    threshold: float = 100.0,
    frequency: AlertFrequency = AlertFrequency.EVERY_TIME,
    cooldown_min: int = 60,
    status: AlertStatus = AlertStatus.ACTIVE,
) -> MagicMock:
    row = MagicMock()
    _data = {
        "rule_id": rule_id,
        "user_id": user_id,
        "symbols": symbols,
        "field": field.value,
        "operator": operator.value,
        "threshold": threshold,
        "frequency": frequency.value,
        "cooldown_min": cooldown_min,
        "status": status.value,
        "created_at": datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
    }
    row.__getitem__ = lambda self, key: _data[key]
    return row


def _make_event_row(
    event_id: UUID,
    rule_id: UUID,
    user_id: UUID,
    symbol: str = "AAPL",
    field: AlertField = AlertField.PRICE,
    operator: AlertOperator = AlertOperator.GT,
    threshold: float = 100.0,
    triggered_value: float = 105.0,
) -> MagicMock:
    row = MagicMock()
    _data = {
        "event_id": event_id,
        "rule_id": rule_id,
        "user_id": user_id,
        "symbol": symbol,
        "triggered_at": datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
        "field_snapshot": field.value,
        "operator_snapshot": operator.value,
        "threshold_snapshot": threshold,
        "triggered_value": triggered_value,
    }
    row.__getitem__ = lambda self, key: _data[key]
    return row


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="")
    return pool


@pytest.fixture
def client(mock_pool: AsyncMock) -> DbClient:
    c = DbClient("postgresql://test:test@localhost/test")
    c._pool = mock_pool
    return c


# ── connect / close ────────────────────────────────────────────────────────────


class TestDbClientLifecycle:
    def test_pool_property_raises_before_connect(self) -> None:
        c = DbClient("postgresql://test:test@localhost/test")
        with pytest.raises(RuntimeError, match="call connect"):
            _ = c.pool

    @pytest.mark.asyncio
    async def test_connect_creates_pool(self) -> None:
        c = DbClient("postgresql://test:test@localhost/test")
        with patch(
            "rule_engine.infrastructure.db.client.asyncpg.create_pool", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = AsyncMock()
            await c.connect()
            mock_create.assert_awaited_once()
            assert c._pool is not None

    @pytest.mark.asyncio
    async def test_close_closes_pool(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        await client.close()
        mock_pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_is_noop_when_not_connected(self) -> None:
        c = DbClient("postgresql://test:test@localhost/test")
        await c.close()  # Should not raise


# ── get_active_rules ───────────────────────────────────────────────────────────


class TestGetActiveRules:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.fetch.return_value = []
        result = await client.get_active_rules()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_mapped_rules(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        rule_id, user_id = uuid4(), uuid4()
        mock_pool.fetch.return_value = [
            _make_rule_row(rule_id, user_id, ["AAPL", "MSFT"])
        ]
        rules = await client.get_active_rules()
        assert len(rules) == 1
        assert rules[0].rule_id == rule_id
        assert rules[0].user_id == user_id
        assert rules[0].symbols == ["AAPL", "MSFT"]
        assert rules[0].status == AlertStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_queries_with_active_status_value(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        await client.get_active_rules()
        call_args = mock_pool.fetch.call_args[0]
        assert "ACTIVE" in call_args


# ── insert_alert_event ─────────────────────────────────────────────────────────


class TestInsertAlertEvent:
    @pytest.mark.asyncio
    async def test_executes_insert(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        event = UserAlertEvent(
            rule_id=uuid4(),
            user_id=uuid4(),
            symbol="AAPL",
            triggered_at=datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC),
            field_snapshot=AlertField.PRICE,
            operator_snapshot=AlertOperator.GT,
            threshold_snapshot=100.0,
            triggered_value=105.0,
        )
        await client.insert_alert_event(event)
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_correct_values(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        rule_id, user_id = uuid4(), uuid4()
        triggered_at = datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)
        event = UserAlertEvent(
            rule_id=rule_id,
            user_id=user_id,
            symbol="TSLA",
            triggered_at=triggered_at,
            field_snapshot=AlertField.VOLUME_ZSCORE,
            operator_snapshot=AlertOperator.GT,
            threshold_snapshot=3.0,
            triggered_value=4.5,
        )
        await client.insert_alert_event(event)
        args = mock_pool.execute.call_args[0]
        assert rule_id in args
        assert user_id in args
        assert "TSLA" in args
        assert 4.5 in args


# ── get_or_create_user ─────────────────────────────────────────────────────────


class TestGetOrCreateUser:
    @pytest.mark.asyncio
    async def test_returns_uuid_from_row(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        expected_id = uuid4()
        row = MagicMock()
        row.__getitem__ = lambda self, key: expected_id
        mock_pool.fetchrow.return_value = row
        result = await client.get_or_create_user(telegram_id=123456)
        assert result == expected_id

    @pytest.mark.asyncio
    async def test_passes_telegram_id(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        expected_id = uuid4()
        row = MagicMock()
        row.__getitem__ = lambda self, key: expected_id
        mock_pool.fetchrow.return_value = row
        await client.get_or_create_user(telegram_id=999)
        args = mock_pool.fetchrow.call_args[0]
        assert 999 in args


# ── insert_rule ────────────────────────────────────────────────────────────────


class TestInsertRule:
    @pytest.mark.asyncio
    async def test_returns_rule_id(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        expected_id = uuid4()
        row = MagicMock()
        row.__getitem__ = lambda self, key: expected_id
        mock_pool.fetchrow.return_value = row
        rule = UserAlertRule(
            user_id=uuid4(),
            symbols=["AAPL"],
            field=AlertField.PRICE,
            operator=AlertOperator.GT,
            threshold=150.0,
        )
        result = await client.insert_rule(rule)
        assert result == expected_id


# ── update_rule_status ─────────────────────────────────────────────────────────


class TestUpdateRuleStatus:
    @pytest.mark.asyncio
    async def test_executes_update(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        rule_id = uuid4()
        await client.update_rule_status(rule_id, AlertStatus.PAUSED)
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_status_value_and_rule_id(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        rule_id = uuid4()
        await client.update_rule_status(rule_id, AlertStatus.TRIGGERED)
        args = mock_pool.execute.call_args[0]
        assert "TRIGGERED" in args
        assert rule_id in args


# ── get_rules_for_user ─────────────────────────────────────────────────────────


class TestGetRulesForUser:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rules(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.fetch.return_value = []
        result = await client.get_rules_for_user(uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_rules_for_user(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        rule_id, user_id = uuid4(), uuid4()
        mock_pool.fetch.return_value = [
            _make_rule_row(rule_id, user_id, ["*"], status=AlertStatus.ACTIVE)
        ]
        rules = await client.get_rules_for_user(user_id)
        assert len(rules) == 1
        assert rules[0].user_id == user_id


# ── get_alert_history ──────────────────────────────────────────────────────────


class TestGetAlertHistory:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_events(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.fetch.return_value = []
        result = await client.get_alert_history(uuid4())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_events_without_symbol_filter(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        event_id, rule_id, user_id = uuid4(), uuid4(), uuid4()
        mock_pool.fetch.return_value = [
            _make_event_row(event_id, rule_id, user_id)
        ]
        events = await client.get_alert_history(user_id)
        assert len(events) == 1
        assert events[0].event_id == event_id

    @pytest.mark.asyncio
    async def test_passes_uppercased_symbol_to_query(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.fetch.return_value = []
        await client.get_alert_history(uuid4(), symbol="aapl")
        args = mock_pool.fetch.call_args[0]
        assert "AAPL" in args


# ── delete_rule ────────────────────────────────────────────────────────────────


class TestDeleteRule:
    @pytest.mark.asyncio
    async def test_returns_true_when_deleted(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.execute.return_value = "DELETE 1"
        result = await client.delete_rule(uuid4(), uuid4())
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.execute.return_value = "DELETE 0"
        result = await client.delete_rule(uuid4(), uuid4())
        assert result is False

    @pytest.mark.asyncio
    async def test_passes_both_ids_to_query(
        self, client: DbClient, mock_pool: AsyncMock
    ) -> None:
        mock_pool.execute.return_value = "DELETE 1"
        rule_id, user_id = uuid4(), uuid4()
        await client.delete_rule(rule_id, user_id)
        args = mock_pool.execute.call_args[0]
        assert rule_id in args
        assert user_id in args
