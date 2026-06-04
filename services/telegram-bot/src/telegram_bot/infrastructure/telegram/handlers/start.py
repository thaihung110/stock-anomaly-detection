"""Phase 4: /start handler — UPSERT chat_id so alert-service can route to user."""
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from telegram_bot.domain.ports import IAlertRepository
from telegram_bot.infrastructure.telegram import BotApp

logger = structlog.get_logger(__name__)

_HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

_WELCOME = (
    "👋 Welcome to *Stock Anomaly Detection Bot*\n\n"
    "You'll receive Telegram alerts for unusual market activity on stocks "
    "you watch.\n\n"
    "Get started:\n"
    "• `/watch AAPL` — subscribe to a symbol\n"
    "• `/watchlist` — see what you're watching\n"
    "• `/preferences` — view notification settings\n"
    "• `/help` — full command list"
)
_PRIVATE_ONLY = (
    "Please /start me in a *private* chat — I need a direct line to "
    "deliver your alerts."
)


def _make_start_handler(repo: IAlertRepository) -> _HandlerFunc:
    async def handle(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if msg is None or chat is None or user is None:
            return
        if chat.type != "private":
            await msg.reply_text(_PRIVATE_ONLY, parse_mode="Markdown")
            return
        user_id = await repo.upsert_chat_id(user.id, chat.id)
        await msg.reply_text(_WELCOME, parse_mode="Markdown")
        logger.info(
            "command_start",
            telegram_id=user.id,
            chat_id=chat.id,
            user_id=str(user_id),
        )

    return handle


def register_start_handler(app: BotApp, repo: IAlertRepository) -> None:
    app.add_handler(CommandHandler("start", _make_start_handler(repo)))
