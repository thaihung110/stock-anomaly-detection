"""Kafka consumer for system-generated anomaly alerts (``alerts.raw`` / ``alerts.confirmed``).

Registers exactly one handler depending on ``cfg.delivery_source``:

* ``CONFIRMED`` (LLM agent on) — consumes ``ConfirmedAlertEvent`` from
  ``alerts.confirmed``, rendered with the "AI Analysis" block as Telegram HTML.
  Follow-up re-check updates (``alerts.followup``) are handled separately in
  ``consumers/followups.py``.
* ``RAW`` (default, LLM agent off/not deployed) — consumes ``AlertEvent``
  from ``alerts.raw`` with the legacy Markdown format.

Both ``fan_out`` (fan-out enabled) and ``deliver_admin_only`` (fan-out
disabled) live on ``AlertDeliveryService`` (see ``services/delivery.py``), so
this module only picks which entry point to call per ``cfg.enable_fanout``.
"""
from __future__ import annotations

import structlog

from alert_service.bootstrap import cfg, container, router
from alert_service.core.config import DeliverySource
from alert_service.core.schema import AlertEvent, ConfirmedAlertEvent
from alert_service.services.formatter import format_confirmed_message, format_message

logger = structlog.get_logger(__name__)


if cfg.delivery_source == DeliverySource.CONFIRMED:

    @router.subscriber(cfg.kafka.input_topic, group_id=cfg.kafka.consumer_group)
    async def handle_confirmed(event: ConfirmedAlertEvent) -> None:
        delivery = container.require_delivery()
        if cfg.enable_fanout:
            await delivery.fan_out(event)
        else:
            await delivery.deliver_admin_only(
                event, format_confirmed_message(event), parse_mode="HTML"
            )

        # Stage D: append anomaly_judgement revision=0. Best-effort — an analytics
        # write failure must never block user delivery (already done above).
        try:
            await container.judgement_writer.append_initial(event, cfg)
        except Exception as exc:
            logger.error(
                "judgement_initial_write_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                error=str(exc),
            )

else:

    @router.subscriber(cfg.kafka.input_topic, group_id=cfg.kafka.consumer_group)
    async def handle_alert(event: AlertEvent) -> None:
        delivery = container.require_delivery()
        if cfg.enable_fanout:
            await delivery.fan_out(event)
        else:
            await delivery.deliver_admin_only(event, format_message(event), parse_mode="Markdown")
