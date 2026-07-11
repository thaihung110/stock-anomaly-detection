"""Smoke test for ``bootstrap.py`` — the process-wide cfg/router/container singletons."""
from __future__ import annotations

from faststream.kafka.fastapi import KafkaRouter

from alert_service.bootstrap import cfg, container, router
from alert_service.container import Container
from alert_service.core.config import Settings


def test_bootstrap_exposes_settings_router_and_container() -> None:
    assert isinstance(cfg, Settings)
    assert isinstance(router, KafkaRouter)
    assert isinstance(container, Container)
    assert container.history_writer is not None
    assert container.judgement_writer is not None
