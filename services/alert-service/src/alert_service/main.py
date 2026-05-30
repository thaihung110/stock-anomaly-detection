"""Alert-service entrypoint.

Phase 3: when ``ENABLE_FANOUT`` is true the service uses
``AlertDeliveryService`` to fan an alert out to every matching subscriber.
When false it preserves the legacy behavior of sending the single Telegram
message to ``cfg.telegram_chat_id`` and writing one ``fact_alert_history``
row with ``user_id = NULL``.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import asyncpg
import structlog
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from faststream.kafka.fastapi import KafkaRouter

from alert_service.config import Settings
from alert_service.delivery import AlertDeliveryService
from alert_service.dlq_producer import DLQPublisher
from alert_service.formatter import format_message
from alert_service.history_writer import append_alert_history, init_iceberg
from alert_service.rate_limiter import PerChatRateLimiter
from alert_service.schema import AlertEvent, DLQReason
from alert_service.subscriber_cache import SubscriberCache
from alert_service.subscriber_repository import SubscriberRepository
from alert_service.telegram_client import (
    SharedTelegramClient,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
    build_telegram_client,
)

logger = structlog.get_logger(__name__)

cfg = Settings()
router = KafkaRouter(cfg.kafka_bootstrap_servers)

_telegram: SharedTelegramClient | None = None
_pg_pool: asyncpg.Pool | None = None
_cache: SubscriberCache | None = None
_delivery: AlertDeliveryService | None = None
_rate_limiter: PerChatRateLimiter | None = None
_dlq: DLQPublisher | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _telegram, _pg_pool, _cache, _delivery, _rate_limiter, _dlq
    await asyncio.to_thread(init_iceberg, cfg)
    _telegram = build_telegram_client(cfg)
    _rate_limiter = PerChatRateLimiter(
        global_rate=cfg.telegram_global_rate,
        per_chat_rate=cfg.telegram_per_chat_rate,
        cache_size=cfg.rate_limiter_cache_size,
        time_period=cfg.rate_limiter_time_period,
    )

    if cfg.dlq_enabled:
        _dlq = DLQPublisher(cfg.kafka_bootstrap_servers, cfg.alerts_failed_topic)
        await _dlq.start()

    if cfg.enable_fanout:
        _pg_pool = await asyncpg.create_pool(cfg.pg_dsn, min_size=2, max_size=10)
        repo = SubscriberRepository(_pg_pool)
        _cache = SubscriberCache(repo, ttl_sec=cfg.subscriber_cache_ttl_sec)
        _delivery = AlertDeliveryService(
            _telegram, _cache, cfg, rate_limiter=_rate_limiter, dlq=_dlq
        )
        logger.info(
            "alert_service_started",
            mode="fanout",
            kafka_topic=cfg.kafka_input_topic,
            cache_ttl_sec=cfg.subscriber_cache_ttl_sec,
            dlq_enabled=cfg.dlq_enabled,
            global_rate=cfg.telegram_global_rate,
            per_chat_rate=cfg.telegram_per_chat_rate,
        )
    else:
        logger.info(
            "alert_service_started",
            mode="admin_only",
            kafka_topic=cfg.kafka_input_topic,
            dlq_enabled=cfg.dlq_enabled,
        )

    async with router.lifespan_context(_):
        yield

    if _dlq is not None:
        await _dlq.stop()
    if _pg_pool is not None:
        await _pg_pool.close()
    logger.info("alert_service_stopped")


app = FastAPI(lifespan=lifespan)
app.include_router(router)


@router.subscriber(cfg.kafka_input_topic, group_id=cfg.kafka_consumer_group)
async def handle_alert(event: AlertEvent) -> None:
    if _delivery is not None:
        await _delivery.fan_out(event)
        return

    # Legacy path — fan-out disabled. Still benefits from the proactive
    # rate-limiter and DLQ so a flaky Telegram doesn't silently drop alerts.
    if _telegram is None:
        logger.error(
            "alert_dropped_telegram_not_initialized",
            alert_id=event.alert_id,
            symbol=event.symbol,
        )
        return
    text = format_message(event)
    if _rate_limiter is not None:
        await _rate_limiter.acquire(cfg.telegram_chat_id)
    try:
        await _telegram.send_message(cfg.telegram_chat_id, text)
    except TelegramError as exc:
        logger.error(
            "alert_dropped_telegram_failure",
            alert_id=event.alert_id,
            symbol=event.symbol,
            rule=event.rule_name.value,
        )
        if _dlq is not None:
            if isinstance(exc, TelegramRateLimitError):
                reason = DLQReason.RATE_LIMIT
            elif isinstance(exc, TelegramPermanentError):
                reason = DLQReason.PERMANENT
            else:
                reason = DLQReason.TRANSPORT
            await _dlq.publish_failure(
                event=event,
                recipient=cfg.telegram_chat_id,
                reason=reason,
                error=str(exc),
                attempt_count=cfg.telegram_retry_attempts,
            )
        return

    try:
        await append_alert_history(event, cfg)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "alert_history_write_failed",
            alert_id=event.alert_id,
            symbol=event.symbol,
            error=str(exc),
        )
        if _dlq is not None:
            await _dlq.publish_failure(
                event=event,
                recipient=cfg.telegram_chat_id,
                reason=DLQReason.HISTORY_WRITE,
                error=str(exc),
                attempt_count=cfg.telegram_retry_attempts,
            )


@app.post("/internal/reload-subscribers")
async def reload_subscribers() -> JSONResponse:
    """Invalidate the subscriber cache.

    Called by the Telegram bot whenever it mutates ``user_preferences`` or
    ``user_watchlist`` so the next alert sees fresh routing data.
    """
    if _cache is None:
        return JSONResponse(
            {"status": "noop", "reason": "fanout_disabled"},
            status_code=status.HTTP_409_CONFLICT,
        )
    _cache.invalidate()
    return JSONResponse({"status": "ok", "stats": _cache.stats})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
