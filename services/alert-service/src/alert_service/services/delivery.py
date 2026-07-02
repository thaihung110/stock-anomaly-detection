"""Fan-out + admin-only delivery for system alerts.

For each ``AlertEvent`` consumed from Kafka, the service writes a single
batched ``fact_alert_history`` row for *every* recipient first (audit trail),
then sends one Telegram message per recipient in parallel. When fan-out is
disabled, ``deliver_admin_only`` does the same for a single admin recipient.

Ordering invariant: Iceberg history write always precedes Telegram delivery.
A history write failure aborts delivery entirely and routes the event to the
DLQ. A Telegram failure for one recipient does not abort others — each
Telegram failure is individually DLQ'd.

If the subscriber list is empty, the admin chat acts as a safety net so an
alert is never silently dropped.

Phase 4 — ``fan_out`` and ``deliver_admin_only`` share the same
history-write-then-send-then-classify-and-DLQ implementation
(``_write_history_or_abort`` / ``_send_and_dlq``) so the two delivery modes
cannot drift apart.

Phase 5 additions:
* Proactive ``PerChatRateLimiter`` before every Telegram send.
* DLQ append to ``alerts.failed`` on history-write failure or Telegram
  permanent/rate-limit/transport failure.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

import structlog
from alert_service.infrastructure.telegram_client import (
    SharedTelegramClient,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)

from alert_service.core.config import Settings
from alert_service.infrastructure.dlq_producer import DLQPublisher
from alert_service.infrastructure.iceberg.history_writer import HistoryWriter
from alert_service.services.formatter import (
    format_confirmed_message,
    format_custom_message,
    format_followup_message,
    format_message,
)
from alert_service.services.rate_limiter import PerChatRateLimiter
from alert_service.core.schema import (
    AlertEvent,
    ConfirmedAlertEvent,
    CustomAlertEvent,
    DLQReason,
    FollowUpEvent,
)
from alert_service.services.subscriber_cache import SubscriberCache
from alert_service.infrastructure.subscriber_repository import Subscriber

logger = structlog.get_logger(__name__)


class AlertDeliveryService:
    """Coordinates subscriber lookup, history write, rate-limiting, Telegram delivery, and DLQ."""

    def __init__(
        self,
        telegram: SharedTelegramClient,
        cache: SubscriberCache | None,
        cfg: Settings,
        history_writer: HistoryWriter,
        rate_limiter: PerChatRateLimiter | None = None,
        dlq: DLQPublisher | None = None,
    ) -> None:
        self._telegram = telegram
        self._cache = cache
        self._cfg = cfg
        self._history_writer = history_writer
        self._rate_limiter = rate_limiter
        self._dlq = dlq

    async def fan_out(self, event: AlertEvent) -> None:
        """Fan an alert out to all matching subscribers.

        History is written for all recipients in a single batched Iceberg
        commit *before* any Telegram message is sent (audit-trail-first).
        A history failure aborts the entire fan-out; a per-recipient Telegram
        failure is logged and DLQ'd without aborting the others.
        """
        cache = self._require_cache("fan_out")
        try:
            subscribers = await cache.get(event.symbol)
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

        # Write history FIRST — audit trail before any delivery attempt.
        user_ids: list[str | None] = [str(sub.user_id) for sub in recipients]
        wrote_history = await self._write_history_or_abort(event, user_ids, failure_chat_id=0)
        if not wrote_history:
            return

        # Fan out Telegram — per-recipient failure does not abort others.
        renderer, parse_mode = _FAN_OUT_RENDERERS.get(type(event), _DEFAULT_RENDERER)
        text = renderer(event)
        results = await asyncio.gather(
            *(
                self._send_and_dlq(event, sub.chat_id, text, parse_mode, subscriber=sub)
                for sub in recipients
            ),
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

    async def deliver_admin_only(
        self, event: AlertEvent, text: str, parse_mode: str | None
    ) -> None:
        """Legacy admin-only delivery (fan-out disabled): history write then one send.

        Used by the raw and confirmed handlers so the audit-trail-first
        ordering and DLQ behaviour stay identical to ``fan_out`` regardless of
        message type — both share ``_write_history_or_abort`` /
        ``_send_and_dlq``.
        """
        admin_chat_id = self._cfg.telegram.chat_id
        wrote_history = await self._write_history_or_abort(
            event, user_ids=[None], failure_chat_id=admin_chat_id
        )
        if not wrote_history:
            return

        await self._send_and_dlq(event, admin_chat_id, text, parse_mode, subscriber=None)

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
        cache = self._require_cache("deliver_followup")
        try:
            subscribers = await cache.get(event.symbol)
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
                await self._telegram.send_message(sub.chat_id, text, parse_mode="HTML")
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
        admin: int | str | None = self._cfg.telegram.chat_id

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

    def _require_cache(self, caller: str) -> SubscriberCache:
        """Return ``self._cache``, raising if the service was built without one.

        Used instead of ``assert self._cache is not None`` because ``assert``
        is removed entirely under ``python -O`` / ``PYTHONOPTIMIZE=1`` — this
        invariant must hold in every build, optimized or not.
        """
        if self._cache is None:
            raise RuntimeError(f"{caller} requires a cache (construct with enable_fanout=True)")
        return self._cache

    def _resolve_recipients(self, subscribers: list[Subscriber]) -> list[Subscriber]:
        """Return subscribers who opted in to receive this alert.

        Empty list means all users have opted out (mode=OFF or not in watchlist)
        — the caller logs ``fanout_no_recipients`` and silently drops the alert.
        Admin-chat fallback is intentionally absent: in fan-out mode the admin
        must register with system_alert_mode=ALL to receive alerts.
        """
        return list(subscribers)

    async def _write_history_or_abort(
        self,
        event: AlertEvent,
        user_ids: list[str | None],
        *,
        failure_chat_id: int | str,
    ) -> bool:
        """Write the audit-trail row(s) via ``self._history_writer``.

        Returns ``True`` on success. On failure the error is logged and (for
        non-timeout failures) DLQ'd; the caller must abort delivery — this
        already happened, so it returns ``False`` rather than raising.

        Shared by ``fan_out`` (many recipients, one batch) and
        ``deliver_admin_only`` (single recipient, ``user_ids=[None]``) so both
        keep the exact same audit-trail-first / timeout-never-DLQ invariant.
        """
        try:
            await self._history_writer.append_batch(event, user_ids=user_ids)
        except TimeoutError:
            # Unknown commit state — must not DLQ to avoid duplicate rows on replay.
            logger.error(
                "alert_history_timeout_unknown_state",
                alert_id=event.alert_id,
                symbol=event.symbol,
            )
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "alert_history_write_failed",
                alert_id=event.alert_id,
                symbol=event.symbol,
                error=str(exc),
            )
            await self._dlq_publish(
                event, None, failure_chat_id, DLQReason.HISTORY_WRITE, str(exc), actual_attempts=0
            )
            return False
        return True

    async def _send_and_dlq(
        self,
        event: AlertEvent,
        chat_id: int | str,
        text: str,
        parse_mode: str | None,
        *,
        subscriber: Subscriber | None,
    ) -> None:
        """Send one Telegram message and DLQ on failure. History has already been written."""
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire(chat_id)

        try:
            await self._telegram.send_message(chat_id, text, parse_mode=parse_mode)
        except TelegramError as exc:
            reason = _classify_failure(exc)
            logger.error(
                "alert_delivery_failed",
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
                actual_attempts=self._cfg.telegram.retry_attempts,
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


def _render_confirmed(event: AlertEvent) -> str:
    """Adapt ``format_confirmed_message`` (``ConfirmedAlertEvent -> str``) to the
    ``AlertEvent -> str`` shape ``_FAN_OUT_RENDERERS`` needs. Only ever called
    for events looked up by the exact ``ConfirmedAlertEvent`` key below.
    """
    assert isinstance(event, ConfirmedAlertEvent)
    return format_confirmed_message(event)


# Renderer + parse_mode per concrete event type, keyed by exact ``type(event)``.
# ConfirmedAlertEvent carries the LLM "AI Analysis" block and is rendered as
# HTML (LLM output may contain MarkdownV2 special chars); plain AlertEvent
# keeps the legacy Markdown rendering. Adding a new AlertEvent subclass only
# requires a new dict entry here — ``fan_out`` never needs to change.
_DEFAULT_RENDERER: tuple[Callable[[AlertEvent], str], str | None] = (format_message, "Markdown")
_FAN_OUT_RENDERERS: dict[type[AlertEvent], tuple[Callable[[AlertEvent], str], str | None]] = {
    ConfirmedAlertEvent: (_render_confirmed, "HTML"),
    AlertEvent: _DEFAULT_RENDERER,
}
