"""Tests for custom alert delivery path (ADR-001 SoC refactor).

Covers:
- format_custom_message formatting and batch-daily field note
- AlertDeliveryService.deliver_custom routing, rate-limiting, and error logging
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alert_service.telegram_client import TelegramError, TelegramPermanentError, TelegramRateLimitError

from alert_service.config import Settings
from alert_service.delivery import AlertDeliveryService
from alert_service.formatter import format_custom_message
from alert_service.schema import CustomAlertEvent


def _event(
    symbol: str = "AAPL",
    field: str = "price",
    operator: str = ">",
    threshold: float = 100.0,
    triggered_value: float = 150.0,
    chat_id: int | str | None = 1001,
) -> CustomAlertEvent:
    return CustomAlertEvent(
        event_id=str(uuid4()),
        rule_id=str(uuid4()),
        user_id=str(uuid4()),
        chat_id=chat_id,
        symbol=symbol,
        field=field,
        operator=operator,
        threshold=threshold,
        triggered_value=triggered_value,
        triggered_at="2026-05-18T10:00:00+00:00",
    )


def _cfg(
    *,
    admin_chat: int | str = "ADMIN",
    enable_per_user_routing: bool = False,
) -> Settings:
    return Settings(
        telegram_bot_token="t",
        telegram_chat_id=admin_chat,
        enable_fanout=True,
        enable_per_user_routing=enable_per_user_routing,
    )


# ── format_custom_message ─────────────────────────────────────────────────────


class TestFormatCustomMessage:
    def test_contains_symbol_and_value(self) -> None:
        msg = format_custom_message(_event(symbol="TSLA", triggered_value=200.1234))
        assert "TSLA" in msg
        assert "200.1234" in msg

    def test_contains_field_operator_threshold(self) -> None:
        msg = format_custom_message(_event(field="price", operator=">", threshold=100.0))
        assert "price" in msg
        assert ">" in msg
        assert "100.0" in msg

    def test_batch_daily_rsi_includes_note(self) -> None:
        msg = format_custom_message(_event(field="rsi_14"))
        assert "end-of-previous-day" in msg

    def test_batch_daily_bb_position_includes_note(self) -> None:
        msg = format_custom_message(_event(field="bb_position"))
        assert "end-of-previous-day" in msg

    def test_non_batch_field_has_no_note(self) -> None:
        msg = format_custom_message(_event(field="price"))
        assert "end-of-previous-day" not in msg

    def test_volume_zscore_has_no_note(self) -> None:
        msg = format_custom_message(_event(field="volume_zscore"))
        assert "end-of-previous-day" not in msg


# ── deliver_custom — routing ──────────────────────────────────────────────────


class TestDeliverCustomRouting:
    @pytest.mark.asyncio
    async def test_sends_to_event_chat_id_when_routing_enabled(self) -> None:
        telegram = AsyncMock()
        cache = AsyncMock()
        cfg = _cfg(enable_per_user_routing=True)
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event(chat_id=1001))

        telegram.send_message.assert_awaited_once()
        assert telegram.send_message.call_args[0][0] == 1001

    @pytest.mark.asyncio
    async def test_sends_to_admin_when_routing_disabled(self) -> None:
        telegram = AsyncMock()
        cache = AsyncMock()
        cfg = _cfg(admin_chat="ADMIN_CHAT", enable_per_user_routing=False)
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event(chat_id=9999))

        assert telegram.send_message.call_args[0][0] == "ADMIN_CHAT"

    @pytest.mark.asyncio
    async def test_none_chat_id_falls_back_to_admin_when_routing_on(self) -> None:
        telegram = AsyncMock()
        cache = AsyncMock()
        cfg = _cfg(admin_chat="ADMIN_CHAT", enable_per_user_routing=True)
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event(chat_id=None))

        assert telegram.send_message.call_args[0][0] == "ADMIN_CHAT"

    @pytest.mark.asyncio
    async def test_drops_silently_when_no_chat_and_no_admin(self) -> None:
        telegram = AsyncMock()
        cache = AsyncMock()
        cfg = Settings(
            telegram_bot_token="t",
            telegram_chat_id="",
            enable_per_user_routing=True,
        )
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event(chat_id=None))

        telegram.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sends_with_parse_mode_none(self) -> None:
        telegram = AsyncMock()
        cache = AsyncMock()
        cfg = _cfg()
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event())

        kwargs = telegram.send_message.call_args[1]
        assert kwargs.get("parse_mode") is None


# ── deliver_custom — rate-limiter ─────────────────────────────────────────────


class TestDeliverCustomRateLimiter:
    @pytest.mark.asyncio
    async def test_rate_limiter_called_before_send(self) -> None:
        call_order: list[str] = []

        telegram = AsyncMock()
        telegram.send_message.side_effect = lambda *a, **kw: call_order.append("send") or None

        rate_limiter = AsyncMock()
        rate_limiter.acquire.side_effect = lambda chat_id: call_order.append("rate") or None

        cache = AsyncMock()
        cfg = _cfg()
        delivery = AlertDeliveryService(telegram, cache, cfg, rate_limiter=rate_limiter)

        await delivery.deliver_custom(_event())

        assert call_order == ["rate", "send"]

    @pytest.mark.asyncio
    async def test_no_rate_limiter_send_still_works(self) -> None:
        telegram = AsyncMock()
        cache = AsyncMock()
        cfg = _cfg()
        delivery = AlertDeliveryService(telegram, cache, cfg, rate_limiter=None)

        await delivery.deliver_custom(_event())

        telegram.send_message.assert_awaited_once()


# ── deliver_custom — telegram failure ────────────────────────────────────────


class TestDeliverCustomFailure:
    @pytest.mark.asyncio
    async def test_telegram_error_does_not_raise(self) -> None:
        telegram = AsyncMock()
        telegram.send_message.side_effect = TelegramError("boom")
        cache = AsyncMock()
        cfg = _cfg()
        delivery = AlertDeliveryService(telegram, cache, cfg)

        # Must not raise — custom alerts have user_alert_events as durable log
        await delivery.deliver_custom(_event())

    @pytest.mark.asyncio
    async def test_rate_limit_error_does_not_raise(self) -> None:
        telegram = AsyncMock()
        telegram.send_message.side_effect = TelegramRateLimitError("429")
        cache = AsyncMock()
        cfg = _cfg()
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event())

    @pytest.mark.asyncio
    async def test_permanent_error_does_not_raise(self) -> None:
        telegram = AsyncMock()
        telegram.send_message.side_effect = TelegramPermanentError("400")
        cache = AsyncMock()
        cfg = _cfg()
        delivery = AlertDeliveryService(telegram, cache, cfg)

        await delivery.deliver_custom(_event())


# ── Schema contract ───────────────────────────────────────────────────────────


class TestSchemaContract:
    """CustomAlertEvent must have the canonical field set (Kafka contract)."""

    _EXPECTED_FIELDS = frozenset({
        "event_id", "rule_id", "user_id", "chat_id", "symbol",
        "field", "operator", "threshold", "triggered_value", "triggered_at",
    })

    def test_alert_service_custom_event_has_expected_fields(self) -> None:
        assert set(CustomAlertEvent.model_fields) == self._EXPECTED_FIELDS

    def test_model_dump_roundtrip_preserves_values(self) -> None:
        original = _event(symbol="aapl", triggered_value=200.5, chat_id=1001)
        reloaded = CustomAlertEvent(**original.model_dump())
        assert reloaded.symbol == "AAPL"
        assert reloaded.triggered_value == 200.5
        assert reloaded.chat_id == 1001

    def test_symbol_is_uppercased_on_parse(self) -> None:
        ev = CustomAlertEvent(
            event_id="id-1",
            rule_id="id-2",
            user_id="id-3",
            chat_id=None,
            symbol="tsla",
            field="price",
            operator=">",
            threshold=100.0,
            triggered_value=150.0,
            triggered_at="2026-05-18T10:00:00+00:00",
        )
        assert ev.symbol == "TSLA"
