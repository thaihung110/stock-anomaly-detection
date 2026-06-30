"""Tests for AlertPublisher — mocks FastStream publisher objects."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

from llm_agent.infrastructure.publisher import AlertPublisher
from llm_agent.schema import (
    AlertSeverity,
    ConfirmedAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    RuleName,
)


def _confirmed() -> ConfirmedAlertEvent:
    return ConfirmedAlertEvent(
        alert_id="pub-test-001",
        symbol="AAPL",
        event_ts="2026-06-01T14:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=4.8,
        threshold=3.0,
        context_snapshot={"z_price": 4.8},
        llm_judgement=LLMJudgement.EXPLAINED,
    )


def _followup() -> FollowUpEvent:
    return FollowUpEvent(
        ref_alert_id="pub-test-001",
        symbol="AAPL",
        prev_judgement=LLMJudgement.UNEXPLAINED,
        new_judgement=LLMJudgement.EXPLAINED,
        emitted_at="2026-06-01T14:20:00Z",
    )


class TestAlertPublisher:
    async def test_publish_confirmed_calls_pub_with_bytes(self) -> None:
        confirmed_pub = AsyncMock()
        publisher = AlertPublisher(confirmed_pub, AsyncMock())
        await publisher.publish_confirmed(_confirmed())
        confirmed_pub.publish.assert_awaited_once()
        payload = confirmed_pub.publish.call_args[0][0]
        assert isinstance(payload, bytes)
        assert b"pub-test-001" in payload
        assert b"EXPLAINED" in payload

    async def test_publish_confirmed_payload_round_trips(self) -> None:
        confirmed_pub = AsyncMock()
        publisher = AlertPublisher(confirmed_pub, AsyncMock())
        await publisher.publish_confirmed(_confirmed())
        raw = confirmed_pub.publish.call_args[0][0]
        data = json.loads(raw.decode())
        restored = ConfirmedAlertEvent.model_validate(data)
        assert restored.alert_id == "pub-test-001"
        assert restored.llm_judgement == LLMJudgement.EXPLAINED

    async def test_publish_followup_calls_followup_pub(self) -> None:
        followup_pub = AsyncMock()
        publisher = AlertPublisher(AsyncMock(), followup_pub)
        await publisher.publish_followup(_followup())
        followup_pub.publish.assert_awaited_once()
        payload = followup_pub.publish.call_args[0][0]
        assert isinstance(payload, bytes)
        assert b"EXPLAINED" in payload

    async def test_publish_confirmed_does_not_touch_followup_pub(self) -> None:
        followup_pub = AsyncMock()
        publisher = AlertPublisher(AsyncMock(), followup_pub)
        await publisher.publish_confirmed(_confirmed())
        followup_pub.publish.assert_not_awaited()

    async def test_publish_followup_does_not_touch_confirmed_pub(self) -> None:
        confirmed_pub = AsyncMock()
        publisher = AlertPublisher(confirmed_pub, AsyncMock())
        await publisher.publish_followup(_followup())
        confirmed_pub.publish.assert_not_awaited()
