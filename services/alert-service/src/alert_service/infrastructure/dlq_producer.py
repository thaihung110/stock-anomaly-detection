"""Dead-letter publisher for failed alert deliveries (Phase 5).

When ``AlertDeliveryService`` cannot get an alert through to a recipient, the
original event + failure context is appended to the ``alerts.failed`` Kafka
topic instead of being silently dropped. Operators can replay the topic once
the underlying issue (Telegram outage, bad chat_id, Iceberg unavailable) is
resolved.

The publisher is intentionally fire-and-forget on the caller side (it never
re-raises): a delivery worker should not crash because the DLQ itself is
unavailable. Failures-to-DLQ are counted in ``dlq_publish_failed_total``
and logged at ERROR.
"""
from __future__ import annotations

import time

import structlog
from aiokafka import AIOKafkaProducer

from alert_service.core.schema import (
    AlertEvent,
    DLQReason,
    FailedAlertEnvelope,
    FailedRecipient,
)
from alert_service.infrastructure.subscriber_repository import Subscriber

logger = structlog.get_logger(__name__)


class DLQPublisher:
    """Owns an ``AIOKafkaProducer`` writing to ``alerts.failed``.

    Lifecycle is managed by the service entrypoint (FastAPI lifespan): call
    :meth:`start` before any publish, :meth:`stop` on shutdown.
    """

    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        if not bootstrap_servers:
            raise ValueError("bootstrap_servers must be non-empty")
        if not topic:
            raise ValueError("topic must be non-empty")
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            enable_idempotence=True,
            acks="all",
            compression_type="gzip",
        )
        await producer.start()
        self._producer = producer
        logger.info("dlq_producer_started", topic=self._topic)

    async def stop(self) -> None:
        if self._producer is None:
            return
        try:
            await self._producer.stop()
        finally:
            self._producer = None
            logger.info("dlq_producer_stopped", topic=self._topic)

    async def publish_failure(
        self,
        event: AlertEvent,
        recipient: Subscriber | int | str | None,
        reason: DLQReason,
        error: str,
        attempt_count: int,
    ) -> None:
        """Best-effort append a failure record to ``alerts.failed``.

        ``recipient`` may be:
          * ``Subscriber`` — real subscriber row (user_id + chat_id),
          * ``int | str`` — admin-chat fallback (no user_id),
          * ``None`` — fan-out had no recipients at all.
        """
        if self._producer is None:
            logger.error("dlq_publish_skipped_not_started", alert_id=event.alert_id)
            return

        envelope = FailedAlertEnvelope(
            original_event=event,
            recipient=_to_failed_recipient(recipient),
            reason=reason,
            error=error[:1024],
            failed_at_ms=int(time.time() * 1000),
            attempt_count=attempt_count,
        )
        payload = envelope.model_dump_json().encode("utf-8")
        key = event.symbol.encode("utf-8")

        try:
            await self._producer.send_and_wait(self._topic, value=payload, key=key)
        except Exception as exc:  # noqa: BLE001 — DLQ must never propagate
            logger.error(
                "dlq_publish_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                reason=reason.value,
                error=str(exc),
            )
            return

        logger.info(
            "dlq_published",
            alert_id=event.alert_id,
            symbol=event.symbol,
            reason=reason.value,
        )


def _to_failed_recipient(
    recipient: Subscriber | int | str | None,
) -> FailedRecipient | None:
    if recipient is None:
        return None
    if isinstance(recipient, Subscriber):
        return FailedRecipient(user_id=str(recipient.user_id), chat_id=recipient.chat_id)
    return FailedRecipient(user_id=None, chat_id=recipient)
