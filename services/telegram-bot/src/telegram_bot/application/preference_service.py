"""PreferenceService — manages per-user notification preferences."""
import structlog

from telegram_bot.domain.preferences import SystemAlertMode, UserPreferences
from telegram_bot.domain.ports import (
    IAlertRepository,
    IAlertServiceClient,
    IPreferenceRepository,
)

logger = structlog.get_logger(__name__)


class PreferenceService:
    def __init__(
        self,
        repo: IPreferenceRepository,
        user_repo: IAlertRepository,
        alert_client: IAlertServiceClient,
    ) -> None:
        self._repo = repo
        self._user_repo = user_repo
        self._alert_client = alert_client

    async def get_preferences(self, telegram_id: int) -> UserPreferences:
        user_id = await self._user_repo.get_or_create_user(telegram_id)
        return await self._repo.get(user_id)

    async def set_system_alert_mode(
        self, telegram_id: int, chat_id: int, mode: SystemAlertMode
    ) -> None:
        # upsert_chat_id ensures chat_id is set so alert-service can deliver
        user_id = await self._user_repo.upsert_chat_id(telegram_id, chat_id)
        await self._repo.set_mode(user_id, mode)
        await self._alert_client.reload_subscribers()
        logger.info("preferences_mode_set", telegram_id=telegram_id, mode=mode.value)

    async def toggle_custom_alerts(
        self, telegram_id: int, chat_id: int, enabled: bool
    ) -> None:
        user_id = await self._user_repo.upsert_chat_id(telegram_id, chat_id)
        await self._repo.set_custom_enabled(user_id, enabled)
        await self._alert_client.reload_subscribers()
        logger.info(
            "preferences_custom_enabled_set",
            telegram_id=telegram_id,
            enabled=enabled,
        )
