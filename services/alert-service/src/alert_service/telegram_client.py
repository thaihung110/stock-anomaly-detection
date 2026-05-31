"""Thin shim re-exporting the shared Telegram client for alert-service.

The implementation lives in ``services/shared/telegram_client`` so the same
retry and 429-Retry-After policy is used by ``alert-service`` and
``rule-engine``. This module is kept so existing imports like
``from alert_service.telegram_client import TelegramError`` continue to work.
"""
from __future__ import annotations

from telegram_client import (
    SharedTelegramClient,
    TelegramConfig,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)

from alert_service.config import Settings


def build_telegram_client(cfg: Settings) -> SharedTelegramClient:
    """Construct a ``SharedTelegramClient`` from service ``Settings``."""
    return SharedTelegramClient(
        TelegramConfig(
            bot_token=cfg.telegram_bot_token,
            api_base_url=cfg.telegram_api_base_url,
            retry_attempts=cfg.telegram_retry_attempts,
            retry_base_delay=cfg.telegram_retry_base_delay,
        )
    )


__all__ = [
    "SharedTelegramClient",
    "TelegramError",
    "TelegramPermanentError",
    "TelegramRateLimitError",
    "build_telegram_client",
]
