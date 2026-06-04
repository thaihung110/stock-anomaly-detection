"""Unit tests for PreferenceService (Phase 4)."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from telegram_bot.application.preference_service import PreferenceService
from telegram_bot.domain.preferences import SystemAlertMode, UserPreferences


def _make_svc():
    user_id = uuid4()
    user_repo = AsyncMock()
    user_repo.get_or_create_user.return_value = user_id

    repo = AsyncMock()
    repo.get.return_value = UserPreferences(
        user_id=user_id,
        system_alert_mode=SystemAlertMode.WATCHLIST_ONLY,
        custom_alert_enabled=True,
    )

    alert_client = AsyncMock()
    alert_client.reload_subscribers.return_value = True

    return PreferenceService(repo, user_repo, alert_client), repo, alert_client


@pytest.mark.asyncio
async def test_get_preferences_returns_repo_value() -> None:
    svc, _repo, _client = _make_svc()
    prefs = await svc.get_preferences(123)
    assert prefs.system_alert_mode is SystemAlertMode.WATCHLIST_ONLY
    assert prefs.custom_alert_enabled is True


@pytest.mark.asyncio
async def test_set_mode_persists_and_reloads() -> None:
    svc, repo, alert_client = _make_svc()
    await svc.set_system_alert_mode(123, SystemAlertMode.ALL)
    repo.set_mode.assert_awaited_once()
    assert repo.set_mode.await_args.args[1] is SystemAlertMode.ALL
    alert_client.reload_subscribers.assert_awaited_once()


@pytest.mark.asyncio
async def test_toggle_custom_alerts_persists_but_does_not_reload() -> None:
    svc, repo, alert_client = _make_svc()
    await svc.toggle_custom_alerts(123, False)
    repo.set_custom_enabled.assert_awaited_once()
    assert repo.set_custom_enabled.await_args.args[1] is False
    alert_client.reload_subscribers.assert_not_awaited()
