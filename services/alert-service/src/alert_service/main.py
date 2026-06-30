"""Alert-service entrypoint.

Phase 3: when ``ENABLE_FANOUT`` is true the service uses
``AlertDeliveryService`` to fan an alert out to every matching subscriber.
When false it preserves the legacy behavior of sending the single Telegram
message to ``cfg.telegram_chat_id`` and writing one ``fact_alert_history``
row with ``user_id = NULL``.

In both paths the Iceberg history write happens **before** the Telegram send
so the audit trail is durable regardless of delivery outcome.
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

from alert_service.config import DeliverySource, Settings
from alert_service.delivery import AlertDeliveryService, _classify_failure
from alert_service.dlq_producer import DLQPublisher
from alert_service.formatter import (
    format_confirmed_message,
    format_followup_message,
    format_message,
)
from alert_service.history_writer import append_alert_history, close_iceberg, init_iceberg
from alert_service.judgement_writer import (
    append_followup_judgement,
    append_initial_judgement,
    close_judgement_writer,
    init_judgement_writer,
)
from alert_service.rate_limiter import PerChatRateLimiter
from alert_service.schema import (
    AlertEvent,
    ConfirmedAlertEvent,
    CustomAlertEvent,
    DLQReason,
    FollowUpEvent,
)
from alert_service.subscriber_cache import SubscriberCache
from alert_service.subscriber_repository import SubscriberRepository
from alert_service.telegram_client import (
    SharedTelegramClient,
    TelegramError,
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
    # Stage D: ensure-create gold.anomaly_judgement (no-op unless DELIVERY_SOURCE=confirmed).
    await asyncio.to_thread(init_judgement_writer, cfg)
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
        _pg_pool = await asyncpg.create_pool(
            host=cfg.pg_host,
            port=cfg.pg_port,
            database=cfg.pg_database,
            user=cfg.pg_user,
            password=cfg.pg_password.get_secret_value(),
            min_size=2,
            max_size=10,
        )
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
    # Drain both Iceberg write executors — wait for any in-flight commit to finish.
    await asyncio.to_thread(close_iceberg)
    await asyncio.to_thread(close_judgement_writer)
    logger.info("alert_service_stopped")


app = FastAPI(lifespan=lifespan)
app.include_router(router)


async def _deliver_admin(event: AlertEvent, text: str, parse_mode: str | None) -> None:
    """Legacy admin-only delivery (fan-out disabled): history write then one send.

    Shared by the raw and confirmed handlers so the audit-trail-first ordering
    and DLQ behaviour stay identical regardless of message type.
    """
    if _telegram is None:
        logger.error(
            "alert_dropped_telegram_not_initialized",
            alert_id=event.alert_id,
            symbol=event.symbol,
        )
        return

    # Write history FIRST — audit trail before any delivery attempt.
    try:
        await append_alert_history(event, cfg)
    except TimeoutError:
        # Unknown commit state — must not DLQ to avoid duplicate rows on replay.
        logger.error(
            "alert_history_timeout_unknown_state",
            alert_id=event.alert_id,
            symbol=event.symbol,
        )
        return
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
                attempt_count=0,
            )
        return

    if _rate_limiter is not None:
        await _rate_limiter.acquire(cfg.telegram_chat_id)
    try:
        await _telegram.send_message(cfg.telegram_chat_id, text, parse_mode=parse_mode)
    except TelegramError as exc:
        logger.error(
            "alert_dropped_telegram_failure",
            alert_id=event.alert_id,
            symbol=event.symbol,
            rule=event.rule_name.value,
        )
        if _dlq is not None:
            await _dlq.publish_failure(
                event=event,
                recipient=cfg.telegram_chat_id,
                reason=_classify_failure(exc),
                error=str(exc),
                attempt_count=cfg.telegram_retry_attempts,
            )


if cfg.delivery_source == DeliverySource.CONFIRMED:
    # LLM agent ON — consume ConfirmedAlertEvent (alerts.confirmed) + FollowUpEvent
    # (alerts.followup).  Both render the "AI Analysis" block as plain text.
    @router.subscriber(cfg.kafka_input_topic, group_id=cfg.kafka_consumer_group)
    async def handle_confirmed(event: ConfirmedAlertEvent) -> None:
        if _delivery is not None:
            await _delivery.fan_out(event)
        else:
            await _deliver_admin(event, format_confirmed_message(event), parse_mode=None)

        # Stage D: append anomaly_judgement revision=0. Best-effort — an analytics
        # write failure must never block user delivery (already done above).
        try:
            await append_initial_judgement(event, cfg)
        except Exception as exc:
            logger.error(
                "judgement_initial_write_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                error=str(exc),
            )

    @router.subscriber(
        cfg.kafka_followup_topic, group_id=cfg.kafka_followup_consumer_group
    )
    async def handle_followup(event: FollowUpEvent) -> None:
        if _delivery is not None:
            await _delivery.deliver_followup(event)
        elif _telegram is not None:
            if _rate_limiter is not None:
                await _rate_limiter.acquire(cfg.telegram_chat_id)
            try:
                await _telegram.send_message(
                    cfg.telegram_chat_id,
                    format_followup_message(event),
                    parse_mode=None,
                )
            except TelegramError as exc:
                logger.error(
                    "followup_dropped_telegram_failure",
                    ref_alert_id=event.ref_alert_id,
                    symbol=event.symbol,
                    error=str(exc),
                )

        # Stage D: append anomaly_judgement revision=1 (is_flip if verdict changed).
        try:
            await append_followup_judgement(event, cfg)
        except Exception as exc:
            logger.error(
                "judgement_followup_write_failed",
                ref_alert_id=event.ref_alert_id,
                symbol=event.symbol,
                error=str(exc),
            )

else:
    # LLM agent OFF (default) — consume AlertEvent from alerts.raw, legacy format.
    @router.subscriber(cfg.kafka_input_topic, group_id=cfg.kafka_consumer_group)
    async def handle_alert(event: AlertEvent) -> None:
        if _delivery is not None:
            await _delivery.fan_out(event)
            return
        await _deliver_admin(event, format_message(event), parse_mode="Markdown")


@router.subscriber(cfg.kafka_user_alert_topic, group_id=cfg.kafka_user_consumer_group)
async def handle_custom_alert(event: CustomAlertEvent) -> None:
    if _delivery is None:
        logger.error(
            "custom_alert_dropped_delivery_not_initialized",
            event_id=event.event_id,
            symbol=event.symbol,
        )
        return
    await _delivery.deliver_custom(event)


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
