"""Tests for the DLQ producer (Phase 5)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from alert_service.infrastructure.dlq_producer import DLQPublisher
from alert_service.core.schema import AlertEvent, AlertSeverity, DLQReason, RuleName
from alert_service.infrastructure.subscriber_repository import Subscriber


def _event() -> AlertEvent:
    return AlertEvent(
        alert_id="alert-42",
        symbol="MSFT",
        event_ts="2026-05-25T10:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=300.0,
        threshold=250.0,
        context_snapshot={"z": 5.1},
    )


@pytest.mark.unit
def test_constructor_validates_args() -> None:
    with pytest.raises(ValueError):
        DLQPublisher(bootstrap_servers="", topic="x")
    with pytest.raises(ValueError):
        DLQPublisher(bootstrap_servers="kafka:9092", topic="")


@pytest.mark.asyncio
async def test_publish_subscriber_recipient_emits_envelope() -> None:
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    fake_producer = AsyncMock()
    pub._producer = fake_producer  # type: ignore[attr-defined]

    subscriber = Subscriber(user_id=uuid4(), chat_id=12345)
    await pub.publish_failure(
        event=_event(),
        recipient=subscriber,
        reason=DLQReason.RATE_LIMIT,
        error="429 too many requests",
        attempt_count=3,
    )

    fake_producer.send_and_wait.assert_awaited_once()
    call = fake_producer.send_and_wait.await_args
    assert call.args[0] == "alerts.failed"
    assert call.kwargs["key"] == b"MSFT"

    payload = json.loads(call.kwargs["value"])
    assert payload["reason"] == "rate_limit"
    assert payload["recipient"]["chat_id"] == 12345
    assert payload["recipient"]["user_id"] == str(subscriber.user_id)
    assert payload["attempt_count"] == 3
    assert payload["failed_at_ms"] >= 1_000_000_000_000
    assert payload["original_event"]["alert_id"] == "alert-42"


@pytest.mark.asyncio
async def test_admin_chat_recipient_emits_null_user_id() -> None:
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    fake_producer = AsyncMock()
    pub._producer = fake_producer  # type: ignore[attr-defined]

    await pub.publish_failure(
        event=_event(),
        recipient="ADMIN_CHAT",
        reason=DLQReason.PERMANENT,
        error="chat not found",
        attempt_count=1,
    )

    payload = json.loads(fake_producer.send_and_wait.await_args.kwargs["value"])
    assert payload["recipient"]["user_id"] is None
    assert payload["recipient"]["chat_id"] == "ADMIN_CHAT"


@pytest.mark.asyncio
async def test_none_recipient_emits_null_envelope_field() -> None:
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    fake_producer = AsyncMock()
    pub._producer = fake_producer  # type: ignore[attr-defined]

    await pub.publish_failure(
        event=_event(),
        recipient=None,
        reason=DLQReason.TRANSPORT,
        error="connection reset",
        attempt_count=3,
    )

    payload = json.loads(fake_producer.send_and_wait.await_args.kwargs["value"])
    assert payload["recipient"] is None
    assert payload["reason"] == "transport"


@pytest.mark.asyncio
async def test_publish_swallows_kafka_errors() -> None:
    """A DLQ publish must never propagate — only log and metric."""
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    fake_producer = AsyncMock()
    fake_producer.send_and_wait.side_effect = RuntimeError("kafka down")
    pub._producer = fake_producer  # type: ignore[attr-defined]

    # Must not raise.
    await pub.publish_failure(
        event=_event(),
        recipient=None,
        reason=DLQReason.TRANSPORT,
        error="x",
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_publish_before_start_is_noop() -> None:
    """Calling publish_failure before start() must not raise."""
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    await pub.publish_failure(
        event=_event(),
        recipient=None,
        reason=DLQReason.PERMANENT,
        error="x",
        attempt_count=1,
    )


@pytest.mark.asyncio
async def test_error_message_is_truncated() -> None:
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    fake_producer = AsyncMock()
    pub._producer = fake_producer  # type: ignore[attr-defined]

    long_error = "x" * 5000
    await pub.publish_failure(
        event=_event(),
        recipient=None,
        reason=DLQReason.TRANSPORT,
        error=long_error,
        attempt_count=1,
    )

    payload = json.loads(fake_producer.send_and_wait.await_args.kwargs["value"])
    assert len(payload["error"]) == 1024


@pytest.mark.asyncio
async def test_start_stop_idempotent() -> None:
    pub = DLQPublisher("kafka:9092", "alerts.failed")
    # stop() before start() must be a no-op (not raise)
    await pub.stop()

    with patch("alert_service.infrastructure.dlq_producer.AIOKafkaProducer") as fake_cls:
        instance = AsyncMock()
        fake_cls.return_value = instance
        await pub.start()
        await pub.start()  # second call must short-circuit
        assert fake_cls.call_count == 1
        await pub.stop()
        instance.stop.assert_awaited_once()
