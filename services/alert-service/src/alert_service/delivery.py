"""Fan-out delivery for system alerts.

For each ``AlertEvent`` consumed from Kafka, the service writes a single
batched ``fact_alert_history`` row for *every* recipient first (audit trail),
then sends one Telegram message per recipient in parallel.

Ordering invariant: Iceberg history write always precedes Telegram delivery.
A history write failure aborts the fan-out entirely and routes the event to
the DLQ.  A Telegram failure for one recipient does not abort others — each
Telegram failure is individually DLQ'd.

If the subscriber list is empty, the admin chat acts as a safety net so an
alert is never silently dropped.

Phase 5 additions:
* Proactive ``PerChatRateLimiter`` before every Telegram send.
* DLQ append to ``alerts.failed`` on history-write failure or Telegram
  permanent/rate-limit/transport failure.
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
from alert_service.history_writer import append_alert_history_batch
from alert_service.rate_limiter import PerChatRateLimiter
from alert_service.schema import AlertEvent, DLQReason
from alert_service.subscriber_cache import SubscriberCache
from alert_service.subscriber_repository import Subscriber

logger = structlog.get_logger(__name__)


class AlertDeliveryService:
    """Coordinates: subscriber lookup → history write → rate-limit → Telegram send → DLQ on failure."""

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

        History is written for all recipients in a single batched Iceberg
        commit *before* any Telegram message is sent (audit-trail-first).
        A history failure aborts the entire fan-out; a per-recipient Telegram
        failure is logged and DLQ'd without aborting the others.
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
            await self._dlq_publish(
                event, None, 0, DLQReason.SUBSCRIBER_LOOKUP, str(exc), actual_attempts=0
            )
            return

        recipients = self._resolve_recipients(subscribers)
        if not recipients:
            logger.warning("fanout_no_recipients", symbol=event.symbol, alert_id=event.alert_id)
            return

        # Build user_id list for the batch history write.
        user_ids: list[str | None] = [
            str(sub.user_id) if sub is not None else None for sub in recipients
        ]

        # Write history FIRST — audit trail before any delivery attempt.
        try:
            await append_alert_history_batch(event, self._cfg, user_ids=user_ids)
        except asyncio.TimeoutError:
            # Unknown commit state — must not DLQ to avoid duplicate rows on replay.
            logger.error(
                "fanout_history_timeout_unknown_state",
                alert_id=event.alert_id,
                symbol=event.symbol,
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "fanout_history_write_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                error=str(exc),
            )
            await self._dlq_publish(
                event, None, 0, DLQReason.HISTORY_WRITE, str(exc), actual_attempts=0
            )
            return

        # Fan out Telegram — per-recipient failure does not abort others.
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

        ``None`` is a sentinel telling ``_deliver_one`` to use
        ``cfg.telegram_chat_id`` with a NULL ``user_id`` in history.
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
        """Send one Telegram message.  History has already been written."""
        if subscriber is None:
            chat_id: int | str = self._cfg.telegram_chat_id
        else:
            chat_id = subscriber.chat_id

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
                chat_id=str(chat_id),
                reason=reason.value,
                error=str(exc),
            )
            await self._dlq_publish(
                event,
                subscriber,
                chat_id,
                reason,
                str(exc),
                actual_attempts=self._cfg.telegram_retry_attempts,
            )

    async def _dlq_publish(
        self,
        event: AlertEvent,
        subscriber: Subscriber | None,
        chat_id: int | str,
        reason: DLQReason,
        error: str,
        actual_attempts: int = 0,
    ) -> None:
        if self._dlq is None:
            return
        recipient: Subscriber | int | str = subscriber if subscriber is not None else chat_id
        await self._dlq.publish_failure(
            event=event,
            recipient=recipient,
            reason=reason,
            error=error,
            attempt_count=actual_attempts,
        )


def _classify_failure(exc: TelegramError) -> DLQReason:
    if isinstance(exc, TelegramRateLimitError):
        return DLQReason.RATE_LIMIT
    if isinstance(exc, TelegramPermanentError):
        return DLQReason.PERMANENT
    return DLQReason.TRANSPORT
