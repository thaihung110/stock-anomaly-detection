import structlog
from telegram_bot.application.alert_service import AlertService
from telegram_bot.config import Settings
from telegram_bot.infrastructure.db.client import DbClient
from telegram_bot.infrastructure.db.repository import UserAlertRepository
from telegram_bot.infrastructure.rule_engine_client import RuleEngineClient
from telegram_bot.infrastructure.telegram import BotApp
from telegram_bot.infrastructure.telegram.bot_factory import create_application

logger = structlog.get_logger(__name__)


class BotRunner:
    """Use case: start and run the Telegram bot via webhook.

    Wires all infrastructure adapters, attaches DB lifecycle to the Application's
    post_init / post_shutdown hooks, then delegates to run_webhook.
    """

    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        db_client = DbClient(cfg.pg_dsn)
        repo = UserAlertRepository(db_client)
        re_client = RuleEngineClient(cfg.rule_engine_url)
        svc = AlertService(repo, re_client)

        async def _on_startup(_app: BotApp) -> None:
            await db_client.connect()

        async def _on_shutdown(_app: BotApp) -> None:
            await db_client.close()
            await re_client.close()

        self._app = create_application(cfg, svc, _on_startup, _on_shutdown)

    def run(self) -> None:
        """Start the bot, connecting DB on startup and closing on shutdown."""
        logger.info(
            "telegram_bot_starting",
            webhook_url=self._cfg.webhook_url,
            port=self._cfg.app_port,
        )
        self._app.run_webhook(
            listen="0.0.0.0",
            port=self._cfg.app_port,
            url_path=self._cfg.webhook_path,
            webhook_url=self._cfg.webhook_url,
        )
