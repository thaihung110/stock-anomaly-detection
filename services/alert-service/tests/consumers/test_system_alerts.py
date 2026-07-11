"""Tests for ``consumers.system_alerts`` in RAW mode (default: DELIVERY_SOURCE unset).

``handle_alert`` is only defined when ``cfg.delivery_source == RAW`` — see
``test_system_alerts_confirmed.py`` for the CONFIRMED-mode ``handle_confirmed``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import alert_service.consumers.system_alerts as system_alerts
from alert_service.core.schema import AlertEvent, AlertSeverity, RuleName


def _event() -> AlertEvent:
    return AlertEvent(
        alert_id="a1",
        symbol="AAPL",
        event_ts="2026-05-24T10:00:00Z",
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.HIGH,
        triggered_value=200.0,
        threshold=150.0,
        context_snapshot={},
    )


@pytest.mark.asyncio
async def test_handle_alert_calls_fan_out_when_fanout_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_alerts.cfg, "enable_fanout", True)
    delivery = AsyncMock()
    monkeypatch.setattr(system_alerts.container, "delivery", delivery)

    await system_alerts.handle_alert(_event())

    delivery.fan_out.assert_awaited_once()
    delivery.deliver_admin_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_alert_calls_admin_only_when_fanout_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_alerts.cfg, "enable_fanout", False)
    delivery = AsyncMock()
    monkeypatch.setattr(system_alerts.container, "delivery", delivery)

    event = _event()
    await system_alerts.handle_alert(event)

    delivery.deliver_admin_only.assert_awaited_once()
    assert delivery.deliver_admin_only.await_args.args[0] is event
    delivery.fan_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_alert_requires_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_alerts.container, "delivery", None)

    with pytest.raises(RuntimeError, match="lifespan"):
        await system_alerts.handle_alert(_event())
