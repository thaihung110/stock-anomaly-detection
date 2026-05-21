"""Telegram delivery for custom user alert notifications."""
from datetime import datetime

import httpx
import structlog

from rule_engine.config import Settings
from rule_engine.domain.custom_rules import BATCH_DAILY_FIELDS
from rule_engine.domain.models import UserAlertRule
from rule_engine.metrics import telegram_alert_send_failures_total

logger = structlog.get_logger(__name__)


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
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return

    batch_note = " ⚠️ (end-of-previous-day)" if rule.field in BATCH_DAILY_FIELDS else ""
    text = (
        f"⚡ Custom Alert: {symbol}\n"
        f"Field: {rule.field.value}{batch_note}\n"
        f"Condition: {rule.field.value} {rule.operator.value} {rule.threshold}\n"
        f"Current value: {triggered_value:.4f}\n"
        f"Time: {triggered_at.isoformat()}"
    )
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": cfg.telegram_chat_id, "text": text})
            resp.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
        telegram_alert_send_failures_total.inc()
        logger.error(
            "telegram_custom_alert_failed",
            symbol=symbol,
            rule_id=str(rule.rule_id),
            error=str(exc),
        )
