"""Tests for ``main.lifespan()`` — verifies startup/shutdown wiring order.

``lifespan()`` mutates the shared ``bootstrap.container`` singleton directly
(not via a test double), so each test snapshots the container's fields before
running and restores them afterward — otherwise a real ``AlertDeliveryService``
built here would leak into unrelated tests that run later in the same process.
"""
from __future__ import annotations

import contextlib
import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

import alert_service.main as main


def _snapshot_container() -> dict[str, object]:
    return {f.name: getattr(main.container, f.name) for f in dataclasses.fields(main.container)}


def _restore_container(snapshot: dict[str, object]) -> None:
    for name, value in snapshot.items():
        setattr(main.container, name, value)


@contextlib.asynccontextmanager
async def _fake_router_lifespan(_app: object) -> None:
    yield


@pytest.mark.asyncio
async def test_lifespan_admin_only_mode_wires_delivery_without_cache_or_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_container()
    try:
        monkeypatch.setattr(main.cfg, "enable_fanout", False)
        monkeypatch.setattr(main.cfg, "dlq_enabled", False)
        monkeypatch.setattr(main.container.history_writer, "init", MagicMock())
        monkeypatch.setattr(main.container.history_writer, "close", MagicMock())
        monkeypatch.setattr(main.container.judgement_writer, "init", MagicMock())
        monkeypatch.setattr(main.container.judgement_writer, "close", MagicMock())
        monkeypatch.setattr(main, "build_telegram_client", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(main.router, "lifespan_context", _fake_router_lifespan)

        async with main.lifespan(MagicMock()):
            assert main.container.delivery is not None
            assert main.container.cache is None
            assert main.container.dlq is None
            assert main.container.pg_pool is None

        main.container.history_writer.close.assert_called_once()
        main.container.judgement_writer.close.assert_called_once()
    finally:
        _restore_container(snapshot)


@pytest.mark.asyncio
async def test_lifespan_fanout_mode_builds_cache_pool_and_dlq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot_container()
    try:
        monkeypatch.setattr(main.cfg, "enable_fanout", True)
        monkeypatch.setattr(main.cfg, "dlq_enabled", True)
        monkeypatch.setattr(main.container.history_writer, "init", MagicMock())
        monkeypatch.setattr(main.container.history_writer, "close", MagicMock())
        monkeypatch.setattr(main.container.judgement_writer, "init", MagicMock())
        monkeypatch.setattr(main.container.judgement_writer, "close", MagicMock())
        monkeypatch.setattr(main, "build_telegram_client", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(main.router, "lifespan_context", _fake_router_lifespan)

        fake_pool = AsyncMock()
        monkeypatch.setattr(main.asyncpg, "create_pool", AsyncMock(return_value=fake_pool))

        dlq_instance = AsyncMock()
        monkeypatch.setattr(main, "DLQPublisher", MagicMock(return_value=dlq_instance))

        async with main.lifespan(MagicMock()):
            assert main.container.cache is not None
            assert main.container.pg_pool is fake_pool
            assert main.container.dlq is dlq_instance
            dlq_instance.start.assert_awaited_once()

        dlq_instance.stop.assert_awaited_once()
        fake_pool.close.assert_awaited_once()
        main.container.history_writer.close.assert_called_once()
        main.container.judgement_writer.close.assert_called_once()
    finally:
        _restore_container(snapshot)


def test_app_state_container_is_the_shared_singleton() -> None:
    assert main.app.state.container is main.container
