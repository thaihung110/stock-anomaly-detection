from collections.abc import Awaitable, Callable

from telegram_bot.application.alert_service import AlertService
from telegram_bot.config import Settings
from telegram_bot.infrastructure.telegram import BotApp
from telegram_bot.infrastructure.telegram.handlers.alert_commands import register_alert_handlers
from telegram_bot.infrastructure.telegram.handlers.help import register_help_handlers

_LifecycleHook = Callable[[BotApp], Awaitable[None]]


def create_application(
    cfg: Settings,
    svc: AlertService,
    on_startup: _LifecycleHook | None = None,
    on_shutdown: _LifecycleHook | None = None,
) -> BotApp:
    """Build and configure the Telegram bot Application.

    This factory is the single place where python-telegram-bot is wired up.
    Adding a new command = adding a register_*_handlers() call here.
    """
    builder = BotApp.builder().token(cfg.telegram_bot_token)
    if on_startup is not None:
        builder = builder.post_init(on_startup)
    if on_shutdown is not None:
        builder = builder.post_shutdown(on_shutdown)

    app: BotApp = builder.build()
    register_help_handlers(app)
    register_alert_handlers(app, svc)
    return app
