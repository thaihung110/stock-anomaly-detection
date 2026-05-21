

import structlog
from telegram import Update
from telegram_bot.infrastructure.telegram import BotApp
from telegram.ext import CommandHandler, ContextTypes

logger = structlog.get_logger(__name__)

_COMING_SOON = "⏳ This feature is coming soon. Stay tuned!"

_STUB_COMMANDS = (
    "setalert",
    "listalerts",
    "pausealert",
    "resumealert",
    "resetalert",
    "delalert",
    "alerthistory",
)


async def _coming_soon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with a placeholder message for unimplemented commands."""
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(_COMING_SOON)
    user = update.effective_user
    logger.info(
        "stub_command_invoked",
        user_id=user.id if user else None,
        text=update.effective_message.text,
    )


def register_placeholder_handlers(app: BotApp) -> None:
    """Register stub handlers for all custom-alert commands (not yet implemented)."""
    for cmd in _STUB_COMMANDS:
        app.add_handler(CommandHandler(cmd, _coming_soon))
