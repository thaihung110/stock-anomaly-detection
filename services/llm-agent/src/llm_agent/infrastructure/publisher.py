"""Kafka publisher for ConfirmedAlertEvent and FollowUpEvent.

Wraps the FastStream publisher objects created in main.py so that graph
nodes and the main handler have a single, typed interface for publishing.
"""
from __future__ import annotations

from typing import Any

import structlog

from llm_agent.schema import ConfirmedAlertEvent, FollowUpEvent

logger = structlog.get_logger(__name__)


class AlertPublisher:
    """Publishes confirmed alerts and follow-up events to Kafka."""

    def __init__(self, confirmed_pub: Any, followup_pub: Any) -> None:
        self._confirmed = confirmed_pub
        self._followup = followup_pub

    async def publish_confirmed(self, event: ConfirmedAlertEvent) -> None:
        """Publish a ConfirmedAlertEvent to alerts.confirmed."""
        payload: bytes = event.model_dump_json().encode()
        await self._confirmed.publish(payload)
        logger.info(
            "confirmed_published",
            alert_id=event.alert_id,
            symbol=event.symbol,
            judgement=event.llm_judgement.value,
        )

    async def publish_followup(self, event: FollowUpEvent) -> None:
        """Publish a FollowUpEvent to alerts.followup (Stage C)."""
        payload: bytes = event.model_dump_json().encode()
        await self._followup.publish(payload)
        logger.info(
            "followup_published",
            ref_alert_id=event.ref_alert_id,
            symbol=event.symbol,
            new_judgement=event.new_judgement.value,
        )
