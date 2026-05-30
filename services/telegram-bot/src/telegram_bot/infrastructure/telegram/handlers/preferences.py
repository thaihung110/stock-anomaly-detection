"""Phase 4: /systemalerts /preferences /customalerts handlers."""
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from telegram_bot.application.preference_service import PreferenceService
from telegram_bot.domain.preferences import SystemAlertMode
from telegram_bot.infrastructure.telegram import BotApp

logger = structlog.get_logger(__name__)

_HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

_MODE_TOKENS = {
    "all": SystemAlertMode.ALL,
    "watchlist": SystemAlertMode.WATCHLIST_ONLY,
    "watchlist_only": SystemAlertMode.WATCHLIST_ONLY,
    "off": SystemAlertMode.OFF,
}
_USAGE_SYSTEMALERTS = (
    "Usage: /systemalerts <all|watchlist|off>\n"
    "• all — receive every system alert\n"
    "• watchlist — only symbols on your /watchlist (default)\n"
    "• off — never receive system alerts"
)
_USAGE_CUSTOMALERTS = "Usage: /customalerts <on|off>"


def _make_systemalerts_handler(svc: PreferenceService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None or update.effective_chat is None:
            return
        msg = update.effective_message
        args = context.args or []
        if len(args) != 1:
            await msg.reply_text(_USAGE_SYSTEMALERTS)
            return
        mode = _MODE_TOKENS.get(args[0].lower())
        if mode is None:
            await msg.reply_text(_USAGE_SYSTEMALERTS)
            return
        await svc.set_system_alert_mode(update.effective_user.id, update.effective_chat.id, mode)
        await msg.reply_text(
            f"✅ System alerts mode set to *{mode.value}*.", parse_mode="Markdown"
        )

    return handle


def _make_customalerts_handler(svc: PreferenceService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None or update.effective_chat is None:
            return
        msg = update.effective_message
        args = context.args or []
        if len(args) != 1 or args[0].lower() not in {"on", "off"}:
            await msg.reply_text(_USAGE_CUSTOMALERTS)
            return
        enabled = args[0].lower() == "on"
        await svc.toggle_custom_alerts(update.effective_user.id, update.effective_chat.id, enabled)
        state = "enabled" if enabled else "disabled"
        await msg.reply_text(f"✅ Custom alerts {state}.")

    return handle


def _make_preferences_handler(svc: PreferenceService) -> _HandlerFunc:
    async def handle(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        prefs = await svc.get_preferences(update.effective_user.id)
        custom = "on" if prefs.custom_alert_enabled else "off"
        await msg.reply_text(
            "*Your preferences:*\n"
            f"• System alerts: `{prefs.system_alert_mode.value}`\n"
            f"• Custom alerts: `{custom}`\n\n"
            "Change with /systemalerts or /customalerts.",
            parse_mode="Markdown",
        )

    return handle


def register_preferences_handlers(app: BotApp, svc: PreferenceService) -> None:
    app.add_handler(CommandHandler("systemalerts", _make_systemalerts_handler(svc)))
    app.add_handler(CommandHandler("customalerts", _make_customalerts_handler(svc)))
    app.add_handler(CommandHandler("preferences", _make_preferences_handler(svc)))
