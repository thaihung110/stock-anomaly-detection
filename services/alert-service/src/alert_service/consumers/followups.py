"""Kafka consumer for LLM re-check follow-up updates (``alerts.followup``).

Only registered when ``cfg.delivery_source == CONFIRMED`` — the llm-agent
pipeline is the sole producer of this topic, so there is nothing to consume
when the LLM agent is off.

A ``FollowUpEvent`` is an *update* to an alert already delivered, so it never
writes ``fact_alert_history`` (no new detection) — only the opt-in
``gold.anomaly_judgement`` analytics row (Stage D).
"""
from __future__ import annotations

import structlog

from alert_service.bootstrap import cfg, container, router
from alert_service.core.config import DeliverySource
from alert_service.core.schema import FollowUpEvent
from alert_service.infrastructure.telegram_client import TelegramError
from alert_service.services.formatter import format_followup_message

logger = structlog.get_logger(__name__)


if cfg.delivery_source == DeliverySource.CONFIRMED:

    @router.subscriber(cfg.kafka.followup_topic, group_id=cfg.kafka.followup_consumer_group)
    async def handle_followup(event: FollowUpEvent) -> None:
        if cfg.enable_fanout:
            await container.require_delivery().deliver_followup(event)
        elif container.telegram is not None:
            if container.rate_limiter is not None:
                await container.rate_limiter.acquire(cfg.telegram.chat_id)
            try:
                await container.telegram.send_message(
                    cfg.telegram.chat_id,
                    format_followup_message(event),
                    parse_mode="HTML",
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
            await container.judgement_writer.append_followup(event, cfg)
        except Exception as exc:
            logger.error(
                "judgement_followup_write_failed",
                ref_alert_id=event.ref_alert_id,
                symbol=event.symbol,
                error=str(exc),
            )
