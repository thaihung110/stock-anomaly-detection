"""Tests for ``consumers.followups`` — only registered when DELIVERY_SOURCE=confirmed.

Uses the ``confirmed_mode`` fixture (see ``conftest.py``) since ``handle_followup``
is only defined in that mode.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alert_service.core.schema import FollowUpEvent, LLMJudgement
from alert_service.infrastructure.telegram_client import TelegramError


def _followup() -> FollowUpEvent:
    return FollowUpEvent(
        ref_alert_id="c1",
        symbol="NVDA",
        prev_judgement=LLMJudgement.UNEXPLAINED,
        new_judgement=LLMJudgement.EXPLAINED,
        emitted_at="2026-06-01T10:05:00Z",
    )


@pytest.mark.asyncio
async def test_followup_fanout_enabled_calls_deliver_followup(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.followups as followups

    monkeypatch.setattr(followups.cfg, "enable_fanout", True)
    delivery = AsyncMock()
    monkeypatch.setattr(followups.container, "delivery", delivery)
    monkeypatch.setattr(followups.container, "judgement_writer", AsyncMock())

    event = _followup()
    await followups.handle_followup(event)

    delivery.deliver_followup.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_followup_fanout_disabled_sends_via_telegram_directly(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.followups as followups

    monkeypatch.setattr(followups.cfg, "enable_fanout", False)
    telegram = AsyncMock()
    monkeypatch.setattr(followups.container, "telegram", telegram)
    monkeypatch.setattr(followups.container, "rate_limiter", None)
    monkeypatch.setattr(followups.container, "judgement_writer", AsyncMock())

    await followups.handle_followup(_followup())

    telegram.send_message.assert_awaited_once()
    assert telegram.send_message.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_followup_rate_limiter_acquired_before_send(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.followups as followups

    monkeypatch.setattr(followups.cfg, "enable_fanout", False)
    telegram = AsyncMock()
    limiter = AsyncMock()
    monkeypatch.setattr(followups.container, "telegram", telegram)
    monkeypatch.setattr(followups.container, "rate_limiter", limiter)
    monkeypatch.setattr(followups.container, "judgement_writer", AsyncMock())

    await followups.handle_followup(_followup())

    limiter.acquire.assert_awaited_once_with(followups.cfg.telegram.chat_id)


@pytest.mark.asyncio
async def test_followup_telegram_failure_is_logged_not_raised(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.followups as followups

    monkeypatch.setattr(followups.cfg, "enable_fanout", False)
    telegram = AsyncMock()
    telegram.send_message.side_effect = TelegramError("boom")
    monkeypatch.setattr(followups.container, "telegram", telegram)
    monkeypatch.setattr(followups.container, "rate_limiter", None)
    monkeypatch.setattr(followups.container, "judgement_writer", AsyncMock())

    await followups.handle_followup(_followup())  # must not raise


@pytest.mark.asyncio
async def test_followup_appends_judgement_followup(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.followups as followups

    monkeypatch.setattr(followups.cfg, "enable_fanout", True)
    monkeypatch.setattr(followups.container, "delivery", AsyncMock())
    judgement_writer = AsyncMock()
    monkeypatch.setattr(followups.container, "judgement_writer", judgement_writer)

    event = _followup()
    await followups.handle_followup(event)

    judgement_writer.append_followup.assert_awaited_once_with(event, followups.cfg)


@pytest.mark.asyncio
async def test_followup_swallows_judgement_write_exception(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.followups as followups

    monkeypatch.setattr(followups.cfg, "enable_fanout", True)
    delivery = AsyncMock()
    monkeypatch.setattr(followups.container, "delivery", delivery)
    judgement_writer = AsyncMock()
    judgement_writer.append_followup.side_effect = RuntimeError("boom")
    monkeypatch.setattr(followups.container, "judgement_writer", judgement_writer)

    await followups.handle_followup(_followup())  # must not raise

    delivery.deliver_followup.assert_awaited_once()
