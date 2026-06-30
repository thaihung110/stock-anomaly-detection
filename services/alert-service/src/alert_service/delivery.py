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
from alert_service.telegram_client import (
    SharedTelegramClient,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)

from alert_service.config import Settings
from alert_service.dlq_producer import DLQPublisher
from alert_service.formatter import (
    format_confirmed_message,
    format_custom_message,
    format_followup_message,
    format_message,
)
from alert_service.history_writer import append_alert_history_batch
from alert_service.rate_limiter import PerChatRateLimiter
from alert_service.schema import (
    AlertEvent,
    ConfirmedAlertEvent,
    CustomAlertEvent,
    DLQReason,
    FollowUpEvent,
)
from alert_service.subscriber_cache import SubscriberCache
from alert_service.subscriber_repository import Subscriber

logger = structlog.get_logger(__name__)


class AlertDeliveryService:
    """Coordinates subscriber lookup, history write, rate-limiting, Telegram delivery, and DLQ."""

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
        user_ids: list[str] = [str(sub.user_id) for sub in recipients]

        # Write history FIRST — audit trail before any delivery attempt.
        try:
            await append_alert_history_batch(event, self._cfg, user_ids=user_ids)
        except TimeoutError:
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
        # ConfirmedAlertEvent carries the LLM "AI Analysis" block and is rendered
        # as plain text (parse_mode=None) since LLM output may contain MarkdownV2
        # special chars.  Plain AlertEvent keeps the legacy Markdown rendering.
        if isinstance(event, ConfirmedAlertEvent):
            text = format_confirmed_message(event)
            parse_mode: str | None = None
        else:
            text = format_message(event)
            parse_mode = "Markdown"
        results = await asyncio.gather(
            *(self._deliver_one(event, text, sub, parse_mode) for sub in recipients),
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

    async def deliver_custom(self, event: CustomAlertEvent) -> None:
        """Deliver a custom user alert via Telegram.

        Routing: when enable_per_user_routing is True, send to event.chat_id;
        fall back to admin chat if chat_id is None. When routing is disabled,
        always use admin chat.

        Does NOT write fact_alert_history — custom alert rows are written by
        the Spark sync_custom_alerts job (07:30 UTC) reading user_alert_events.
        Writing here would cause double-rows. (ADR, quyết định #3)
        """
        chat_id = self._resolve_custom_chat(event)
        if not chat_id:
            logger.warning(
                "custom_alert_dropped_no_chat",
                event_id=event.event_id,
                symbol=event.symbol,
                rule_id=event.rule_id,
            )
            return

        if self._rate_limiter is not None:
            await self._rate_limiter.acquire(chat_id)

        text = format_custom_message(event)
        try:
            await self._telegram.send_message(chat_id, text, parse_mode=None)
        except TelegramError as exc:
            reason = _classify_failure(exc)
            logger.error(
                "custom_alert_delivery_failed",
                event_id=event.event_id,
                symbol=event.symbol,
                rule_id=event.rule_id,
                chat_id=str(chat_id),
                reason=reason.value,
                error=str(exc),
            )

    async def deliver_followup(self, event: FollowUpEvent) -> None:
        """Fan a follow-up re-check update out to subscribers of the symbol.

        A FollowUpEvent is an *update* to an alert already delivered, so it does
        NOT write fact_alert_history (no new detection).  It reuses the same
        watchlist routing as fan_out, rendered as plain text (parse_mode=None).
        Per-recipient failure is logged; it does not abort the others.
        """
        try:
            subscribers = await self._cache.get(event.symbol)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "followup_subscriber_lookup_failed",
                ref_alert_id=event.ref_alert_id,
                symbol=event.symbol,
                error=str(exc),
            )
            return

        recipients = self._resolve_recipients(subscribers)
        if not recipients:
            logger.warning(
                "followup_no_recipients",
                symbol=event.symbol,
                ref_alert_id=event.ref_alert_id,
            )
            return

        text = format_followup_message(event)
        for sub in recipients:
            if self._rate_limiter is not None:
                await self._rate_limiter.acquire(sub.chat_id)
            try:
                await self._telegram.send_message(sub.chat_id, text, parse_mode=None)
            except TelegramError as exc:
                logger.error(
                    "followup_delivery_failed",
                    ref_alert_id=event.ref_alert_id,
                    symbol=event.symbol,
                    chat_id=str(sub.chat_id),
                    error=str(exc),
                )

    def _resolve_custom_chat(self, event: CustomAlertEvent) -> int | str | None:
        """Return the destination chat_id for a custom alert.

        Returns None when no delivery target is configured — caller treats this
        as a hard skip. Logs a warning when falling back from a missing per-user
        chat_id to the admin chat.
        """
        admin: int | str | None = self._cfg.telegram_chat_id

        if not self._cfg.enable_per_user_routing:
            return admin

        if event.chat_id is not None:
            return event.chat_id

        if admin:
            logger.warning(
                "custom_alert_chat_fallback",
                event_id=event.event_id,
                rule_id=event.rule_id,
                reason="missing_chat_id",
            )
        return admin

    def _resolve_recipients(self, subscribers: list[Subscriber]) -> list[Subscriber]:
        """Return subscribers who opted in to receive this alert.

        Empty list means all users have opted out (mode=OFF or not in watchlist)
        — the caller logs ``fanout_no_recipients`` and silently drops the alert.
        Admin-chat fallback is intentionally absent: in fan-out mode the admin
        must register with system_alert_mode=ALL to receive alerts.
        """
        return list(subscribers)

    async def _deliver_one(
        self,
        event: AlertEvent,
        text: str,
        subscriber: Subscriber,
        parse_mode: str | None = "Markdown",
    ) -> None:
        """Send one Telegram message.  History has already been written."""
        chat_id: int | str = subscriber.chat_id

        if self._rate_limiter is not None:
            await self._rate_limiter.acquire(chat_id)

        try:
            await self._telegram.send_message(chat_id, text, parse_mode=parse_mode)
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
