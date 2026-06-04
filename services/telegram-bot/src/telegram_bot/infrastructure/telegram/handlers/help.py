

import structlog
from telegram import Update
from telegram_bot.infrastructure.telegram import BotApp
from telegram.ext import CommandHandler, ContextTypes

logger = structlog.get_logger(__name__)

_HELP_TEXT = """
*Stock Anomaly Detection Bot*

I notify you when unusual activity is detected in US stocks.

*Account*
/start — Register / refresh your chat for delivery
/help — Show this help message

*Watchlist*
/watch <SYMBOL> — Subscribe to a symbol
/unwatch <SYMBOL> — Remove a symbol
/watchlist — Show your current symbols

*Preferences*
/preferences — View notification settings
/systemalerts <all|watchlist|off> — System alert delivery mode
/customalerts <on|off> — Toggle your custom rule alerts

*Custom alerts*
/setalert <SYMBOL|*> <field> <op> <threshold> [once|every]
/listalerts /pausealert /resumealert /resetalert /delalert
/alerthistory [SYMBOL]
""".strip()


async def help_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(_HELP_TEXT, parse_mode="Markdown")
    user = update.effective_user
    logger.info("command_help", user_id=user.id if user else None)


def register_help_handlers(app: BotApp) -> None:
    app.add_handler(CommandHandler("help", help_command))
