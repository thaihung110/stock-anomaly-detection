"""Fan-out delivery for system alerts.

For each ``AlertEvent`` consumed from Kafka, the service looks up the
subscriber list for the alert's symbol (cached, 60 s TTL) and sends one
Telegram message and one ``fact_alert_history`` row *per recipient*.

If the subscriber list is empty, the admin chat acts as a safety net so an
alert is never silently dropped — this also covers freshly deployed
environments where no user has run ``/start`` yet.

Phase 5 additions:
* Proactive ``PerChatRateLimiter`` before every Telegram send.
* DLQ append to ``alerts.failed`` on permanent/rate-limit/transport failure,
  and on history-write failure after a successful Telegram send.
"""
from __future__ import annotations

import asyncio

import structlog
from telegram_client import (
    SharedTelegramClient,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)

from alert_service.config import Settings
from alert_service.dlq_producer import DLQPublisher
from alert_service.formatter import format_message
from alert_service.history_writer import append_alert_history
from alert_service.rate_limiter import PerChatRateLimiter
from alert_service.schema import AlertEvent, DLQReason
from alert_service.subscriber_cache import SubscriberCache
from alert_service.subscriber_repository import Subscriber

logger = structlog.get_logger(__name__)


class AlertDeliveryService:
    """Coordinates: subscriber lookup → rate-limit → Telegram send → history write → DLQ on failure."""

    def __init__(
        self,
        telegram: SharedTelegramClient,
        cache: SubscriberCache,
        cfg: Settings,
        rate_limiter: PerChatRateLimiter | None = None,
        dlq: DLQPublisher | None = None,
    ) -> None:
        self._telegram = telegram
        self._cache = cache
        self._cfg = cfg
        self._rate_limiter = rate_limiter
        self._dlq = dlq

    async def fan_out(self, event: AlertEvent) -> None:
        """Fan an alert out to all matching subscribers.

        Each recipient gets one Telegram message and one row in
        ``gold.fact_alert_history`` carrying their ``user_id``. A per-recipient
        failure is logged + metered but does not abort the whole fan-out.
        """
        try:
            subscribers = await self._cache.get(event.symbol)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "fanout_subscriber_lookup_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                error=str(exc),
            )
            await self._dlq_publish(event, None, 0, DLQReason.SUBSCRIBER_LOOKUP, str(exc))
            return
        recipients = self._resolve_recipients(subscribers)

        if not recipients:
            logger.warning("fanout_no_recipients", symbol=event.symbol, alert_id=event.alert_id)
            return

        text = format_message(event)
        results = await asyncio.gather(
            *(self._deliver_one(event, text, sub) for sub in recipients),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                logger.error(
                    "fanout_task_unexpected_error",
                    alert_id=event.alert_id,
                    symbol=event.symbol,
                    error=str(result),
                    exc_info=result,
                )

    def _resolve_recipients(self, subscribers: list[Subscriber]) -> list[Subscriber | None]:
        """Return real subscribers, or ``[None]`` to mean the admin-chat fallback.

        ``None`` is a sentinel telling ``_deliver_one`` to use ``cfg.telegram_chat_id``
        with a NULL ``user_id`` in history — preserves the legacy 1-chat behavior
        for environments with no onboarded users.
        """
        if subscribers:
            return list(subscribers)
        if self._cfg.telegram_chat_id:
            return [None]
        return []

    async def _deliver_one(
        self,
        event: AlertEvent,
        text: str,
        subscriber: Subscriber | None,
    ) -> None:
        if subscriber is None:
            chat_id: int = self._cfg.telegram_chat_id
            user_id: str | None = None
        else:
            chat_id = subscriber.chat_id
            user_id = str(subscriber.user_id)

        if self._rate_limiter is not None:
            await self._rate_limiter.acquire(chat_id)

        try:
            await self._telegram.send_message(chat_id, text)
        except TelegramError as exc:
            reason = _classify_failure(exc)
            logger.error(
                "fanout_delivery_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                user_id=user_id,
                reason=reason.value,
                error=str(exc),
            )
            await self._dlq_publish(event, subscriber, chat_id, reason, str(exc))
            return

        try:
            await append_alert_history(event, self._cfg, user_id=user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — history failure must not crash worker
            logger.error(
                "fanout_history_write_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                user_id=user_id,
                error=str(exc),
            )
            await self._dlq_publish(
                event, subscriber, chat_id, DLQReason.HISTORY_WRITE, str(exc)
            )

    async def _dlq_publish(
        self,
        event: AlertEvent,
        subscriber: Subscriber | None,
        chat_id: int,
        reason: DLQReason,
        error: str,
    ) -> None:
        if self._dlq is None:
            return
        recipient: Subscriber | int | str = subscriber if subscriber is not None else chat_id
        await self._dlq.publish_failure(
            event=event,
            recipient=recipient,
            reason=reason,
            error=error,
            attempt_count=self._cfg.telegram_retry_attempts,
        )


def _classify_failure(exc: TelegramError) -> DLQReason:
    if isinstance(exc, TelegramRateLimitError):
        return DLQReason.RATE_LIMIT
    if isinstance(exc, TelegramPermanentError):
        return DLQReason.PERMANENT
    return DLQReason.TRANSPORT
