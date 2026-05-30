"""Telegram delivery for custom user alert notifications.

Phase 2: each rule carries its owner's ``chat_id`` (joined from ``users``
in ``DbClient.get_active_rules``). When ``enable_per_user_routing`` is
true, alerts are delivered to that chat; otherwise — or if the user has
not run /start and ``chat_id`` is ``None`` — they fall back to the admin
``telegram_chat_id``. Fallbacks are logged as ``telegram_chat_fallback``
warnings for observability.
"""
from __future__ import annotations

from datetime import datetime

import structlog
from telegram_client import (
    SharedTelegramClient,
    TelegramConfig,
    TelegramError,
)

from rule_engine.config import Settings
from rule_engine.domain.custom_rules import BATCH_DAILY_FIELDS
from rule_engine.domain.models import UserAlertRule

logger = structlog.get_logger(__name__)

_client_cache: SharedTelegramClient | None = None
_client_cache_key: tuple[str, str, int, float] | None = None


def _get_client(cfg: Settings) -> SharedTelegramClient:
    """Return a process-wide ``SharedTelegramClient`` keyed by config tuple.

    Recreates the client only if config values change — important for tests
    that swap ``Settings`` and for hot-reload scenarios.
    """
    global _client_cache, _client_cache_key
    key = (
        cfg.telegram_bot_token,
        cfg.telegram_api_base_url,
        cfg.telegram_retry_attempts,
        cfg.telegram_retry_base_delay,
    )
    if _client_cache is None or _client_cache_key != key:
        _client_cache = SharedTelegramClient(
            TelegramConfig(
                bot_token=cfg.telegram_bot_token,
                api_base_url=cfg.telegram_api_base_url,
                retry_attempts=cfg.telegram_retry_attempts,
                retry_base_delay=cfg.telegram_retry_base_delay,
            )
        )
        _client_cache_key = key
    return _client_cache


def _format_custom_alert(
    rule: UserAlertRule,
    symbol: str,
    triggered_value: float,
    triggered_at: datetime,
) -> str:
    batch_note = " ⚠️ (end-of-previous-day)" if rule.field in BATCH_DAILY_FIELDS else ""
    return (
        f"⚡ Custom Alert: {symbol}\n"
        f"Field: {rule.field.value}{batch_note}\n"
        f"Condition: {rule.field.value} {rule.operator.value} {rule.threshold}\n"
        f"Current value: {triggered_value:.4f}\n"
        f"Time: {triggered_at.isoformat()}"
    )


def _resolve_chat_id(rule: UserAlertRule, cfg: Settings) -> int | str | None:
    """Pick the destination chat for a custom alert.

    Returns ``None`` (or empty string) when no delivery target is configured
    — caller treats falsy result as a hard skip. Logs a warning when falling
    back from a missing per-user ``chat_id`` to the admin chat.
    """
    admin: int | str | None = cfg.telegram_chat_id

    if not cfg.enable_per_user_routing:
        return admin

    if rule.chat_id is not None:
        return rule.chat_id

    if admin:
        logger.warning(
            "telegram_chat_fallback",
            rule_id=str(rule.rule_id),
            user_id=str(rule.user_id),
            reason="missing_chat_id",
        )
    return admin


async def send_telegram_custom_alert(
    rule: UserAlertRule,
    symbol: str,
    triggered_value: float,
    triggered_at: datetime,
    cfg: Settings,
) -> None:
    """Deliver a custom alert notification via Telegram Bot API.

    Failure is logged and metered but never raises — the immutable event log
    is already written before this function is called.
    """
    if not cfg.telegram_bot_token:
        return

    chat_id = _resolve_chat_id(rule, cfg)
    if not chat_id:
        return

    text = _format_custom_alert(rule, symbol, triggered_value, triggered_at)
    client = _get_client(cfg)
    try:
        await client.send_message(chat_id, text, parse_mode=None)
    except TelegramError as exc:
        logger.error(
            "telegram_custom_alert_failed",
            symbol=symbol,
            rule_id=str(rule.rule_id),
            error=str(exc),
        )
