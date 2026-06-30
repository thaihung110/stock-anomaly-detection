"""Tests for confirmed-mode delivery: fan_out(ConfirmedAlertEvent) + deliver_followup.

Verifies that:
  - fan_out renders a ConfirmedAlertEvent with the AI block (format_confirmed_message)
    and sends it as plain text (parse_mode=None).
  - fan_out still uses format_message + Markdown for plain AlertEvent (backward compat).
  - deliver_followup fans a FollowUpEvent to subscribers as plain text, no history write.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from alert_service.config import Settings
from alert_service.delivery import AlertDeliveryService
from alert_service.schema import (
    AlertEvent,
    AlertSeverity,
    ConfirmedAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    NewsCategory,
    NewsRef,
    RuleName,
)
from alert_service.subscriber_repository import Subscriber


def _cfg() -> Settings:
    return Settings(telegram_bot_token="t", telegram_chat_id="ADMIN", enable_fanout=True)


def _cache(subs: list[Subscriber]) -> AsyncMock:
    cache = AsyncMock()
    cache.get.return_value = subs
    return cache


def _confirmed(judgement: LLMJudgement = LLMJudgement.EXPLAINED) -> ConfirmedAlertEvent:
    return ConfirmedAlertEvent(
        alert_id="alert-c1",
        symbol="NVDA",
        event_ts="2026-06-27T20:00:00Z",
        rule_name=RuleName.VOLUME_RATIO,
        severity=AlertSeverity.HIGH,
        triggered_value=4.2,
        threshold=3.5,
        context_snapshot={"vol_ratio": 4.2},
        llm_judgement=judgement,
        final_explanation="Volume driven by an under-the-radar AI stock story.",
        news_summary="Nvidia poised to disrupt a $1.8T market.",
        news_category=NewsCategory.CORPORATE,
        news_refs=[
            NewsRef(title="NVDA disrupts market", published_at="2026-06-27T11:43:00Z", source="Yahoo")
        ],
    )


def _plain() -> AlertEvent:
    return AlertEvent(
        alert_id="alert-p1",
        symbol="NVDA",
        event_ts="2026-06-27T20:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=-4.7,
        threshold=3.0,
        context_snapshot={"z_price": -4.7},
    )


def _followup(
    prev: LLMJudgement = LLMJudgement.UNEXPLAINED,
    new: LLMJudgement = LLMJudgement.EXPLAINED,
) -> FollowUpEvent:
    return FollowUpEvent(
        ref_alert_id="alert-c1",
        symbol="NVDA",
        prev_judgement=prev,
        new_judgement=new,
        news_summary="Late upgrade confirmed the move.",
        news_refs=[
            NewsRef(title="NVDA upgrade", published_at="2026-06-27T14:45:00Z", source="CNBC")
        ],
        emitted_at="2026-06-27T14:52:00Z",
    )


# ── fan_out with ConfirmedAlertEvent ──────────────────────────────────────────


class TestFanOutConfirmed:
    @pytest.mark.asyncio
    async def test_confirmed_uses_plain_text_parse_mode_none(self) -> None:
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()):
            await delivery.fan_out(_confirmed())

        telegram.send_message.assert_awaited_once()
        assert telegram.send_message.await_args.kwargs["parse_mode"] is None

    @pytest.mark.asyncio
    async def test_confirmed_message_contains_ai_block(self) -> None:
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()):
            await delivery.fan_out(_confirmed(LLMJudgement.EXPLAINED))

        text = telegram.send_message.await_args.args[1]
        assert "EXPLAINED" in text
        assert "NVDA" in text

    @pytest.mark.asyncio
    async def test_confirmed_still_writes_history(self) -> None:
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()) as mock_hist:
            await delivery.fan_out(_confirmed())

        mock_hist.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_plain_alert_uses_markdown(self) -> None:
        """Backward compat: a plain AlertEvent keeps Markdown rendering."""
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()):
            await delivery.fan_out(_plain())

        assert telegram.send_message.await_args.kwargs["parse_mode"] == "Markdown"


# ── deliver_followup ──────────────────────────────────────────────────────────


class TestDeliverFollowup:
    @pytest.mark.asyncio
    async def test_followup_fans_out_to_subscribers(self) -> None:
        telegram = AsyncMock()
        subs = [
            Subscriber(user_id=uuid4(), chat_id=1001),
            Subscriber(user_id=uuid4(), chat_id=2002),
        ]
        delivery = AlertDeliveryService(telegram, _cache(subs), _cfg())

        await delivery.deliver_followup(_followup())

        assert telegram.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_followup_plain_text(self) -> None:
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        await delivery.deliver_followup(_followup())

        assert telegram.send_message.await_args.kwargs["parse_mode"] is None

    @pytest.mark.asyncio
    async def test_followup_no_history_write(self) -> None:
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        with patch("alert_service.delivery.append_alert_history_batch", new=AsyncMock()) as mock_hist:
            await delivery.deliver_followup(_followup())

        mock_hist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_followup_no_recipients_is_silent(self) -> None:
        telegram = AsyncMock()
        delivery = AlertDeliveryService(telegram, _cache([]), _cfg())

        await delivery.deliver_followup(_followup())

        telegram.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_followup_message_contains_symbol(self) -> None:
        telegram = AsyncMock()
        sub = Subscriber(user_id=uuid4(), chat_id=1001)
        delivery = AlertDeliveryService(telegram, _cache([sub]), _cfg())

        await delivery.deliver_followup(
            _followup(LLMJudgement.UNEXPLAINED, LLMJudgement.EXPLAINED)
        )

        text = telegram.send_message.await_args.args[1]
        assert "NVDA" in text
