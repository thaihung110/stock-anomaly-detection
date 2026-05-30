"""Phase 4: /watch /unwatch /watchlist handlers."""
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from telegram_bot.application.watchlist_service import WatchlistService
from telegram_bot.domain.symbol import InvalidSymbolError
from telegram_bot.infrastructure.telegram import BotApp

logger = structlog.get_logger(__name__)

_HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

_USAGE_WATCH = "Usage: /watch <SYMBOL>\nExample: /watch AAPL"
_USAGE_UNWATCH = "Usage: /unwatch <SYMBOL>\nExample: /unwatch AAPL"


def _make_watch_handler(svc: WatchlistService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None or update.effective_chat is None:
            return
        msg = update.effective_message
        args = context.args or []
        if len(args) != 1:
            await msg.reply_text(_USAGE_WATCH)
            return
        try:
            added = await svc.watch(update.effective_user.id, update.effective_chat.id, args[0])
        except InvalidSymbolError as exc:
            await msg.reply_text(f"❌ {exc}")
            return
        symbol = args[0].upper()
        if added:
            await msg.reply_text(f"✅ Added *{symbol}* to your watchlist.", parse_mode="Markdown")
        else:
            await msg.reply_text(f"ℹ️ *{symbol}* is already in your watchlist.", parse_mode="Markdown")

    return handle


def _make_unwatch_handler(svc: WatchlistService) -> _HandlerFunc:
    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None or update.effective_chat is None:
            return
        msg = update.effective_message
        args = context.args or []
        if len(args) != 1:
            await msg.reply_text(_USAGE_UNWATCH)
            return
        try:
            removed = await svc.unwatch(update.effective_user.id, update.effective_chat.id, args[0])
        except InvalidSymbolError as exc:
            await msg.reply_text(f"❌ {exc}")
            return
        symbol = args[0].upper()
        if removed:
            await msg.reply_text(f"🗑 Removed *{symbol}* from your watchlist.", parse_mode="Markdown")
        else:
            await msg.reply_text(f"ℹ️ *{symbol}* was not in your watchlist.", parse_mode="Markdown")

    return handle


def _make_list_handler(svc: WatchlistService) -> _HandlerFunc:
    async def handle(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_user is None:
            return
        msg = update.effective_message
        symbols = await svc.list_watchlist(update.effective_user.id)
        if not symbols:
            await msg.reply_text(
                "Your watchlist is empty. Try `/watch AAPL`.", parse_mode="Markdown"
            )
            return
        body = "\n".join(f"• `{s}`" for s in symbols)
        await msg.reply_text(f"*Your watchlist:*\n{body}", parse_mode="Markdown")

    return handle


def register_watchlist_handlers(app: BotApp, svc: WatchlistService) -> None:
    app.add_handler(CommandHandler("watch", _make_watch_handler(svc)))
    app.add_handler(CommandHandler("unwatch", _make_unwatch_handler(svc)))
    app.add_handler(CommandHandler("watchlist", _make_list_handler(svc)))
