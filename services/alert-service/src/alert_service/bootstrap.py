"""Process-wide singletons created at import time: config, Kafka router, container.

Kept in one module so ``main.py`` and every ``consumers/*`` / ``api/routers/*``
module can import the same ``cfg`` / ``router`` / ``container`` without a
circular import — ``main.py`` imports the consumer/router modules to trigger
their ``@router.subscriber`` / ``@router.get`` registration side effects, so
those modules cannot import ``router`` back from ``main``.
"""
from __future__ import annotations

from faststream.kafka.fastapi import KafkaRouter

from alert_service.container import Container
from alert_service.core.config import Settings
from alert_service.infrastructure.iceberg.history_writer import HistoryWriter
from alert_service.infrastructure.iceberg.judgement_writer import JudgementWriter

cfg = Settings()
router = KafkaRouter(cfg.kafka.bootstrap_servers)
container = Container(history_writer=HistoryWriter(), judgement_writer=JudgementWriter())
