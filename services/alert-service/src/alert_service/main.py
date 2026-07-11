"""Alert-service entrypoint — thin composition root.

Phase 2: all shared state (Telegram client, DB pool, subscriber cache,
delivery service, rate limiter, DLQ publisher, Iceberg writers) lives on one
``Container`` instance (see ``container.py``), stored on ``app.state``. HTTP
routes read it via ``Depends()`` (see ``api/routers/admin.py``); Kafka
consumer handlers (see ``consumers/``) read the same ``container`` object
imported from ``bootstrap.py``. This module only builds the container during
``lifespan()`` and wires routers/consumers together — it holds no delivery
logic of its own.

Phase 3: when ``ENABLE_FANOUT`` is true, delivery uses
``AlertDeliveryService.fan_out`` to fan an alert out to every matching
subscriber. When false it uses ``AlertDeliveryService.deliver_admin_only``,
which preserves the legacy behavior of sending the single Telegram message to
``cfg.telegram.chat_id`` and writing one ``fact_alert_history`` row with
``user_id = NULL``. Both share one implementation (Phase 4) inside
``AlertDeliveryService``.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import asyncpg
import structlog
from fastapi import FastAPI

from alert_service.api.routers.admin import router as admin_router
from alert_service.bootstrap import cfg, container, router
from alert_service.infrastructure.dlq_producer import DLQPublisher
from alert_service.infrastructure.subscriber_repository import SubscriberRepository
from alert_service.infrastructure.telegram_client import build_telegram_client
from alert_service.services.delivery import AlertDeliveryService
from alert_service.services.rate_limiter import PerChatRateLimiter
from alert_service.services.subscriber_cache import SubscriberCache

# Imported for their @router.subscriber registration side effects.
from alert_service.consumers import custom_alerts, followups, system_alerts  # noqa: F401

logger = structlog.get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(container.history_writer.init, cfg)
    # Stage D: ensure-create gold.anomaly_judgement (no-op unless DELIVERY_SOURCE=confirmed).
    await asyncio.to_thread(container.judgement_writer.init, cfg)
    container.telegram = build_telegram_client(cfg)
    container.rate_limiter = PerChatRateLimiter(
        global_rate=cfg.telegram.global_rate,
        per_chat_rate=cfg.telegram.per_chat_rate,
        cache_size=cfg.telegram.rate_limiter_cache_size,
        time_period=cfg.telegram.rate_limiter_time_period,
    )

    if cfg.dlq_enabled:
        container.dlq = DLQPublisher(cfg.kafka.bootstrap_servers, cfg.alerts_failed_topic)
        await container.dlq.start()

    if cfg.enable_fanout:
        pg = cfg.postgres
        container.pg_pool = await asyncpg.create_pool(
            host=pg.host,
            port=pg.port,
            database=pg.database,
            user=pg.user,
            password=pg.password.get_secret_value(),
            min_size=2,
            max_size=10,
        )
        repo = SubscriberRepository(container.pg_pool)
        container.cache = SubscriberCache(repo, ttl_sec=cfg.subscriber_cache_ttl_sec)

    # Constructed unconditionally — Phase 4: deliver_admin_only (fan-out
    # disabled) and fan_out (fan-out enabled) both live on this one service.
    container.delivery = AlertDeliveryService(
        container.telegram,
        container.cache,
        cfg,
        container.history_writer,
        rate_limiter=container.rate_limiter,
        dlq=container.dlq,
    )

    if cfg.enable_fanout:
        logger.info(
            "alert_service_started",
            mode="fanout",
            kafka_topic=cfg.kafka.input_topic,
            cache_ttl_sec=cfg.subscriber_cache_ttl_sec,
            dlq_enabled=cfg.dlq_enabled,
            global_rate=cfg.telegram.global_rate,
            per_chat_rate=cfg.telegram.per_chat_rate,
        )
    else:
        logger.info(
            "alert_service_started",
            mode="admin_only",
            kafka_topic=cfg.kafka.input_topic,
            dlq_enabled=cfg.dlq_enabled,
        )

    async with router.lifespan_context(_):
        yield

    if container.dlq is not None:
        await container.dlq.stop()
    if container.pg_pool is not None:
        await container.pg_pool.close()
    # Drain both Iceberg write executors — wait for any in-flight commit to finish.
    await asyncio.to_thread(container.history_writer.close)
    await asyncio.to_thread(container.judgement_writer.close)
    logger.info("alert_service_stopped")


app = FastAPI(lifespan=lifespan)
app.state.container = container
app.include_router(router)
app.include_router(admin_router)
