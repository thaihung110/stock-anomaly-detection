"""Tests for ``consumers.custom_alerts``."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import alert_service.consumers.custom_alerts as custom_alerts
from alert_service.core.schema import CustomAlertEvent


def _event() -> CustomAlertEvent:
    return CustomAlertEvent(
        event_id=str(uuid4()),
        rule_id=str(uuid4()),
        user_id=str(uuid4()),
        chat_id=1001,
        symbol="AAPL",
        field="price",
        operator=">",
        threshold=100.0,
        triggered_value=150.0,
        triggered_at="2026-05-18T10:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_handle_custom_alert_calls_deliver_custom(monkeypatch: pytest.MonkeyPatch) -> None:
    delivery = AsyncMock()
    monkeypatch.setattr(custom_alerts.container, "delivery", delivery)

    event = _event()
    await custom_alerts.handle_custom_alert(event)

    delivery.deliver_custom.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_handle_custom_alert_requires_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(custom_alerts.container, "delivery", None)

    with pytest.raises(RuntimeError, match="lifespan"):
        await custom_alerts.handle_custom_alert(_event())
