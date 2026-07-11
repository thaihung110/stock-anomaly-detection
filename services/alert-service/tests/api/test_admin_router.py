"""Tests for the admin HTTP router — proves the Depends()/app.state DI split works.

Builds a standalone FastAPI app with just this router and overrides
``get_container`` via ``app.dependency_overrides`` — no monkeypatching of
module globals, unlike the pre-Phase-2 design.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from alert_service.api.routers.admin import get_container, router
from alert_service.container import Container


def _app_with_container(container: Container) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_container] = lambda: container
    return TestClient(app)


def _container(cache: MagicMock | None) -> Container:
    return Container(history_writer=MagicMock(), judgement_writer=MagicMock(), cache=cache)


def test_health_returns_ok() -> None:
    client = _app_with_container(_container(cache=None))

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reload_subscribers_invalidates_cache() -> None:
    cache = MagicMock()
    cache.stats = {"hits": 1, "misses": 0, "entries": 1, "inflight": 0}
    client = _app_with_container(_container(cache=cache))

    resp = client.post("/internal/reload-subscribers")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    cache.invalidate.assert_called_once()


def test_reload_subscribers_noop_when_fanout_disabled() -> None:
    client = _app_with_container(_container(cache=None))

    resp = client.post("/internal/reload-subscribers")

    assert resp.status_code == 409
    assert resp.json()["reason"] == "fanout_disabled"
