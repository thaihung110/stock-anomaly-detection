"""Kafka consumer for user-defined custom alerts (``alerts.user``)."""
from __future__ import annotations

import structlog

from alert_service.bootstrap import cfg, container, router
from alert_service.core.schema import CustomAlertEvent

logger = structlog.get_logger(__name__)


@router.subscriber(cfg.kafka.user_alert_topic, group_id=cfg.kafka.user_consumer_group)
async def handle_custom_alert(event: CustomAlertEvent) -> None:
    await container.require_delivery().deliver_custom(event)
