import contextlib
import time
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI
from faststream.kafka.fastapi import KafkaRouter
from prometheus_client import make_asgi_app

from alert_service.config import Settings
from alert_service.formatter import format_message
from alert_service.history_writer import append_alert_history
from alert_service.metrics import (
    alerts_consumed_total,
    alerts_failed_total,
    alerts_sent_total,
    telegram_latency_seconds,
)
from alert_service.schema import AlertEvent
from alert_service.telegram_client import TelegramClient, TelegramError

logger = structlog.get_logger(__name__)

cfg = Settings()
router = KafkaRouter(cfg.kafka_bootstrap_servers)

_telegram: TelegramClient | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _telegram
    _telegram = TelegramClient(cfg)
    logger.info("alert_service_started", kafka_topic=cfg.kafka_input_topic)
    async with router.lifespan_context(_):
        yield
    logger.info("alert_service_stopped")


app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.mount("/metrics", make_asgi_app())


@router.subscriber(cfg.kafka_input_topic, group_id=cfg.kafka_consumer_group)
async def handle_alert(event: AlertEvent) -> None:
    alerts_consumed_total.inc()
    assert _telegram is not None

    text = format_message(event)
    start = time.monotonic()
    try:
        await _telegram.send_message(text)
        elapsed = time.monotonic() - start
        telegram_latency_seconds.observe(elapsed)
        alerts_sent_total.labels(
            rule_name=event.rule_name.value, severity=event.severity.value
        ).inc()
        await append_alert_history(event, cfg)
    except TelegramError:
        alerts_failed_total.labels(
            rule_name=event.rule_name.value, severity=event.severity.value
        ).inc()
        logger.error(
            "alert_dropped_telegram_failure",
            alert_id=event.alert_id,
            symbol=event.symbol,
            rule=event.rule_name.value,
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
