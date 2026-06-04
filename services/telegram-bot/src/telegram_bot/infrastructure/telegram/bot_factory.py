from collections.abc import Awaitable, Callable

from telegram_bot.application.alert_service import AlertService
from telegram_bot.application.preference_service import PreferenceService
from telegram_bot.application.watchlist_service import WatchlistService
from telegram_bot.config import Settings
from telegram_bot.domain.ports import IAlertRepository
from telegram_bot.infrastructure.telegram import BotApp
from telegram_bot.infrastructure.telegram.handlers.alert_commands import register_alert_handlers
from telegram_bot.infrastructure.telegram.handlers.help import register_help_handlers
from telegram_bot.infrastructure.telegram.handlers.preferences import register_preferences_handlers
from telegram_bot.infrastructure.telegram.handlers.start import register_start_handler
from telegram_bot.infrastructure.telegram.handlers.watchlist_commands import (
    register_watchlist_handlers,
)

_LifecycleHook = Callable[[BotApp], Awaitable[None]]


def create_application(
    cfg: Settings,
    svc: AlertService,
    repo: IAlertRepository,
    watchlist_svc: WatchlistService,
    preference_svc: PreferenceService,
    on_startup: _LifecycleHook | None = None,
    on_shutdown: _LifecycleHook | None = None,
) -> BotApp:
    """Build and configure the Telegram bot Application."""
    builder = BotApp.builder().token(cfg.telegram_bot_token)
    if on_startup is not None:
        builder = builder.post_init(on_startup)
    if on_shutdown is not None:
        builder = builder.post_shutdown(on_shutdown)

    app: BotApp = builder.build()
    register_start_handler(app, repo)
    register_help_handlers(app)
    register_watchlist_handlers(app, watchlist_svc)
    register_preferences_handlers(app, preference_svc)
    register_alert_handlers(app, svc)
    return app
