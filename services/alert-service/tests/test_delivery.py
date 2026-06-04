"""Tests for ``AlertDeliveryService.fan_out``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from alert_service.telegram_client import (
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)

from alert_service.config import Settings
from alert_service.delivery import AlertDeliveryService
from alert_service.schema import AlertEvent, AlertSeverity, DLQReason, RuleName
from alert_service.subscriber_repository import Subscriber


def _event(symbol: str = "AAPL") -> AlertEvent:
    return AlertEvent(
        alert_id="alert-1",
        symbol=symbol,
        event_ts="2026-05-24T10:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=200.0,
        threshold=150.0,
        context_snapshot={"z": 4.5},
    )


def _cfg(*, admin_chat: int | str = "ADMIN") -> Settings:
    return Settings(
        telegram_bot_token="t",
        telegram_chat_id=admin_chat,
        enable_fanout=True,
    )


def _cache(subscribers: list[Subscriber]) -> AsyncMock:
    cache = AsyncMock()
    cache.get.return_value = subscribers
    return cache


# ── Core fan-out behaviour ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fans_out_to_every_subscriber() -> None:
    telegram = AsyncMock()
    uid_a, uid_b = uuid4(), uuid4()
    subs = [Subscriber(user_id=uid_a, chat_id=1001), Subscriber(user_id=uid_b, chat_id=2002)]
    cache = _cache(subs)
    cfg = _cfg()

    delivery = AlertDeliveryService(telegram, cache, cfg)
    with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()) as mock_hist:
        await delivery.fan_out(_event())

    # History written ONCE (batch) before Telegram sends, containing both user_ids.
    mock_hist.assert_awaited_once()
    written_ids = set(mock_hist.await_args.kwargs["user_ids"])
    assert written_ids == {str(uid_a), str(uid_b)}

    assert telegram.send_message.await_count == 2
    chat_ids = {call.args[0] for call in telegram.send_message.await_args_list}
    assert chat_ids == {1001, 2002}


@pytest.mark.asyncio
async def test_no_subscribers_drops_alert() -> None:
    """All users opted out (mode=OFF / not in watchlist) → alert silently dropped.

    Admin-chat fallback was intentionally removed: in fan-out mode the admin
    must register with system_alert_mode=ALL to receive alerts.
    """
    telegram = AsyncMock()
    cache = _cache([])
    cfg = _cfg(admin_chat="ADMIN_CHAT")

    delivery = AlertDeliveryService(telegram, cache, cfg)
    with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()) as mock_hist:
        await delivery.fan_out(_event())

    telegram.send_message.assert_not_awaited()
    mock_hist.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_telegram_failure_does_not_block_others() -> None:
    telegram = AsyncMock()
    telegram.send_message.side_effect = [TelegramError("boom"), None]
    subs = [Subscriber(user_id=uuid4(), chat_id=1001), Subscriber(user_id=uuid4(), chat_id=2002)]
    cache = _cache(subs)
    cfg = _cfg()

    delivery = AlertDeliveryService(telegram, cache, cfg)
    with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()) as mock_hist:
        await delivery.fan_out(_event())

    # History written once before Telegram — not affected by Telegram outcome.
    mock_hist.assert_awaited_once()
    # Both Telegram sends attempted; first fails, second succeeds.
    assert telegram.send_message.await_count == 2


@pytest.mark.asyncio
async def test_history_write_failure_aborts_fan_out() -> None:
    """A batch history write failure must abort the fan-out and route to DLQ."""
    telegram = AsyncMock()
    subs = [Subscriber(user_id=uuid4(), chat_id=1001), Subscriber(user_id=uuid4(), chat_id=2002)]
    cache = _cache(subs)
    cfg = _cfg()
    dlq = AsyncMock()

    history_mock = AsyncMock(side_effect=RuntimeError("iceberg down"))
    delivery = AlertDeliveryService(telegram, cache, cfg, dlq=dlq)
    with patch("alert_service.delivery.append_alert_history_batch", new=history_mock):
        await delivery.fan_out(_event())

    # Telegram must not be called when history fails (audit-before-delivery invariant).
    telegram.send_message.assert_not_awaited()
    dlq.publish_failure.assert_awaited_once()
    kwargs = dlq.publish_failure.await_args.kwargs
    assert kwargs["reason"] is DLQReason.HISTORY_WRITE
    assert kwargs["attempt_count"] == 0


@pytest.mark.asyncio
async def test_history_timeout_aborts_fan_out_without_dlq() -> None:
    """Timeout on history write must abort fan-out but NOT DLQ (unknown commit state)."""
    telegram = AsyncMock()
    subs = [Subscriber(user_id=uuid4(), chat_id=1001)]
    cache = _cache(subs)
    cfg = _cfg()
    dlq = AsyncMock()

    history_mock = AsyncMock(side_effect=TimeoutError())
    delivery = AlertDeliveryService(telegram, cache, cfg, dlq=dlq)
    with patch("alert_service.delivery.append_alert_history_batch", new=history_mock):
        await delivery.fan_out(_event())

    telegram.send_message.assert_not_awaited()
    # Must NOT DLQ on timeout — the commit outcome is unknown and replay would duplicate.
    dlq.publish_failure.assert_not_awaited()


# ── Phase 5 — rate-limit + DLQ wiring ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_acquired_before_send() -> None:
    telegram = AsyncMock()
    subs = [Subscriber(user_id=uuid4(), chat_id=1001), Subscriber(user_id=uuid4(), chat_id=2002)]
    cache = _cache(subs)
    cfg = _cfg()
    limiter = AsyncMock()

    delivery = AlertDeliveryService(telegram, cache, cfg, rate_limiter=limiter)
    with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()):
        await delivery.fan_out(_event())

    assert limiter.acquire.await_count == 2
    acquired_chats = {c.args[0] for c in limiter.acquire.await_args_list}
    assert acquired_chats == {1001, 2002}


@pytest.mark.parametrize(
    "exc, expected_reason",
    [
        (TelegramRateLimitError("429"), DLQReason.RATE_LIMIT),
        (TelegramPermanentError("bad chat"), DLQReason.PERMANENT),
        (TelegramError("timeout"), DLQReason.TRANSPORT),
    ],
)
@pytest.mark.asyncio
async def test_telegram_failure_classified_and_sent_to_dlq(
    exc: TelegramError, expected_reason: DLQReason
) -> None:
    telegram = AsyncMock()
    telegram.send_message.side_effect = exc
    sub = Subscriber(user_id=uuid4(), chat_id=1001)
    cache = _cache([sub])
    cfg = _cfg()
    dlq = AsyncMock()

    delivery = AlertDeliveryService(telegram, cache, cfg, dlq=dlq)
    with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()):
        await delivery.fan_out(_event())

    dlq.publish_failure.assert_awaited_once()
    kwargs = dlq.publish_failure.await_args.kwargs
    assert kwargs["reason"] is expected_reason
    assert kwargs["recipient"] is sub
    assert kwargs["attempt_count"] == cfg.telegram_retry_attempts


@pytest.mark.asyncio
async def test_telegram_failure_for_subscriber_routes_to_dlq() -> None:
    """Permanent Telegram error for a real subscriber is DLQ'd with subscriber chat_id."""
    telegram = AsyncMock()
    telegram.send_message.side_effect = TelegramPermanentError("nope")
    sub = Subscriber(user_id=uuid4(), chat_id=9999)
    cache = _cache([sub])
    cfg = _cfg()
    dlq = AsyncMock()

    delivery = AlertDeliveryService(telegram, cache, cfg, dlq=dlq)
    with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()):
        await delivery.fan_out(_event())

    dlq.publish_failure.assert_awaited_once()
    kwargs = dlq.publish_failure.await_args.kwargs
    assert kwargs["recipient"] == sub
    assert kwargs["reason"] is DLQReason.PERMANENT


@pytest.mark.asyncio
async def test_subscriber_lookup_failure_routes_to_dlq_with_zero_attempts() -> None:
    telegram = AsyncMock()
    cache = AsyncMock()
    cache.get.side_effect = RuntimeError("pg down")
    cfg = _cfg()
    dlq = AsyncMock()

    delivery = AlertDeliveryService(telegram, cache, cfg, dlq=dlq)
    await delivery.fan_out(_event())

    telegram.send_message.assert_not_awaited()
    dlq.publish_failure.assert_awaited_once()
    kwargs = dlq.publish_failure.await_args.kwargs
    assert kwargs["reason"] is DLQReason.SUBSCRIBER_LOOKUP
    assert kwargs["attempt_count"] == 0
