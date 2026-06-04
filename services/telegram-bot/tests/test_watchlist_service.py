"""Unit tests for WatchlistService (Phase 4)."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from telegram_bot.application.watchlist_service import WatchlistService
from telegram_bot.domain.symbol import InvalidSymbolError


def _make_svc(*, add_returns: bool = True, remove_returns: bool = True):
    user_id = uuid4()
    user_repo = AsyncMock()
    user_repo.get_or_create_user.return_value = user_id

    repo = AsyncMock()
    repo.add.return_value = add_returns
    repo.remove.return_value = remove_returns
    repo.list.return_value = ["AAPL", "MSFT"]

    alert_client = AsyncMock()
    alert_client.reload_subscribers.return_value = True

    return WatchlistService(repo, user_repo, alert_client), repo, alert_client


@pytest.mark.asyncio
async def test_watch_adds_and_triggers_reload() -> None:
    svc, repo, alert_client = _make_svc(add_returns=True)
    added = await svc.watch(123, "aapl")
    assert added is True
    repo.add.assert_awaited_once()
    assert repo.add.await_args.args[1] == "AAPL"
    alert_client.reload_subscribers.assert_awaited_once()


@pytest.mark.asyncio
async def test_watch_when_already_present_does_not_reload() -> None:
    svc, _repo, alert_client = _make_svc(add_returns=False)
    added = await svc.watch(123, "AAPL")
    assert added is False
    alert_client.reload_subscribers.assert_not_awaited()


@pytest.mark.asyncio
async def test_watch_rejects_invalid_symbol() -> None:
    svc, repo, alert_client = _make_svc()
    with pytest.raises(InvalidSymbolError):
        await svc.watch(123, "AAPL.US")
    repo.add.assert_not_awaited()
    alert_client.reload_subscribers.assert_not_awaited()


@pytest.mark.asyncio
async def test_unwatch_removes_and_reloads() -> None:
    svc, _repo, alert_client = _make_svc(remove_returns=True)
    removed = await svc.unwatch(123, "AAPL")
    assert removed is True
    alert_client.reload_subscribers.assert_awaited_once()


@pytest.mark.asyncio
async def test_unwatch_missing_does_not_reload() -> None:
    svc, _repo, alert_client = _make_svc(remove_returns=False)
    removed = await svc.unwatch(123, "AAPL")
    assert removed is False
    alert_client.reload_subscribers.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_returns_symbols() -> None:
    svc, _repo, _alert_client = _make_svc()
    out = await svc.list_watchlist(123)
    assert out == ["AAPL", "MSFT"]
