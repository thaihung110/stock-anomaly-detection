

import structlog
from telegram import Update
from telegram_bot.infrastructure.telegram import BotApp
from telegram.ext import CommandHandler, ContextTypes

logger = structlog.get_logger(__name__)

_HELP_TEXT = """
*Stock Anomaly Detection Bot*

I notify you when unusual activity is detected in US stocks.

*Available commands:*
/start — Show this welcome message
/help — Show this help message

*Coming soon:*
/setalert — Set a custom price or indicator alert
/listalerts — List your active alerts
/pausealert — Pause an alert
/resumealert — Resume a paused alert
/delalert — Delete an alert
/alerthistory — View your alert history
""".strip()


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — send personalised welcome message."""
    if update.effective_message is None:
        return
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.effective_message.reply_text(
        f"👋 Hello, {name}!\n\n{_HELP_TEXT}",
        parse_mode="Markdown",
    )
    logger.info("command_start", user_id=user.id if user else None)


async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — send the help text."""
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(_HELP_TEXT, parse_mode="Markdown")
    user = update.effective_user
    logger.info("command_help", user_id=user.id if user else None)


def register_help_handlers(app: BotApp) -> None:
    """Register /start and /help command handlers on the bot application."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
