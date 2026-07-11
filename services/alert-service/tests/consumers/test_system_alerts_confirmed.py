"""Tests for ``consumers.system_alerts`` in CONFIRMED mode.

Uses the ``confirmed_mode`` fixture (see ``conftest.py``) to reload the module
with ``DELIVERY_SOURCE=confirmed`` so ``handle_confirmed`` is defined.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from alert_service.core.schema import AlertSeverity, ConfirmedAlertEvent, LLMJudgement, RuleName


def _confirmed() -> ConfirmedAlertEvent:
    return ConfirmedAlertEvent(
        alert_id="c1",
        symbol="NVDA",
        event_ts="2026-06-01T10:00:00Z",
        rule_name=RuleName.VOLUME_RATIO,
        severity=AlertSeverity.HIGH,
        triggered_value=4.0,
        threshold=3.0,
        context_snapshot={},
        llm_judgement=LLMJudgement.EXPLAINED,
    )


@pytest.mark.asyncio
async def test_handle_confirmed_calls_fan_out_when_enabled(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.system_alerts as system_alerts

    monkeypatch.setattr(system_alerts.cfg, "enable_fanout", True)
    delivery = AsyncMock()
    monkeypatch.setattr(system_alerts.container, "delivery", delivery)
    monkeypatch.setattr(system_alerts.container, "judgement_writer", AsyncMock())

    await system_alerts.handle_confirmed(_confirmed())

    delivery.fan_out.assert_awaited_once()
    delivery.deliver_admin_only.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_confirmed_calls_admin_only_when_disabled(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.system_alerts as system_alerts

    monkeypatch.setattr(system_alerts.cfg, "enable_fanout", False)
    delivery = AsyncMock()
    monkeypatch.setattr(system_alerts.container, "delivery", delivery)
    monkeypatch.setattr(system_alerts.container, "judgement_writer", AsyncMock())

    await system_alerts.handle_confirmed(_confirmed())

    delivery.deliver_admin_only.assert_awaited_once()
    assert delivery.deliver_admin_only.await_args.kwargs["parse_mode"] == "HTML"
    delivery.fan_out.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_confirmed_appends_judgement_initial(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.system_alerts as system_alerts

    monkeypatch.setattr(system_alerts.cfg, "enable_fanout", True)
    monkeypatch.setattr(system_alerts.container, "delivery", AsyncMock())
    judgement_writer = AsyncMock()
    monkeypatch.setattr(system_alerts.container, "judgement_writer", judgement_writer)

    event = _confirmed()
    await system_alerts.handle_confirmed(event)

    judgement_writer.append_initial.assert_awaited_once_with(event, system_alerts.cfg)


@pytest.mark.asyncio
async def test_handle_confirmed_swallows_judgement_write_exception(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An analytics write failure must never block user delivery."""
    import alert_service.consumers.system_alerts as system_alerts

    monkeypatch.setattr(system_alerts.cfg, "enable_fanout", True)
    delivery = AsyncMock()
    monkeypatch.setattr(system_alerts.container, "delivery", delivery)
    judgement_writer = AsyncMock()
    judgement_writer.append_initial.side_effect = RuntimeError("iceberg down")
    monkeypatch.setattr(system_alerts.container, "judgement_writer", judgement_writer)

    await system_alerts.handle_confirmed(_confirmed())  # must not raise

    delivery.fan_out.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_confirmed_requires_delivery(
    confirmed_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import alert_service.consumers.system_alerts as system_alerts

    monkeypatch.setattr(system_alerts.container, "delivery", None)

    with pytest.raises(RuntimeError, match="lifespan"):
        await system_alerts.handle_confirmed(_confirmed())
