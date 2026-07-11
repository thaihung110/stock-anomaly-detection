"""Fixture for testing the CONFIRMED-mode branch of consumer registration.

``consumers/system_alerts.py`` and ``consumers/followups.py`` register
different Kafka handlers depending on ``cfg.delivery_source``, decided at
*import time* (``if cfg.delivery_source == DeliverySource.CONFIRMED: ...``).
To exercise the CONFIRMED branch in-process we must:

1. Set ``DELIVERY_SOURCE=confirmed`` in the environment.
2. Reload ``bootstrap`` — recreates ``cfg`` / ``router`` / ``container`` with
   the new delivery_source.
3. Reload ``system_alerts`` / ``followups`` — they do
   ``from alert_service.bootstrap import cfg, container, router``, which
   binds names at import time, so they must be reloaded *after* bootstrap to
   rebind to the fresh objects and re-evaluate the if/else branch.

Teardown reverses this so later tests (which assume the RAW-mode default)
aren't affected by leftover module state.
"""
from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest

import alert_service.bootstrap as bootstrap
import alert_service.consumers.followups as followups
import alert_service.consumers.system_alerts as system_alerts


@pytest.fixture
def confirmed_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DELIVERY_SOURCE", "confirmed")
    importlib.reload(bootstrap)
    importlib.reload(system_alerts)
    importlib.reload(followups)

    yield

    # Explicitly revert (don't rely on monkeypatch's own teardown timing,
    # which runs after this fixture's teardown code) before reloading back.
    monkeypatch.delenv("DELIVERY_SOURCE", raising=False)
    importlib.reload(bootstrap)
    importlib.reload(system_alerts)
    importlib.reload(followups)
