"""PreferenceRepository — adapter satisfying IPreferenceRepository port."""
from uuid import UUID

from telegram_bot.domain.preferences import SystemAlertMode, UserPreferences
from telegram_bot.infrastructure.db.client import DbClient


class PreferenceRepository:
    def __init__(self, client: DbClient) -> None:
        self._client = client

    async def get(self, user_id: UUID) -> UserPreferences:
        return await self._client.preferences_get(user_id)

    async def set_mode(self, user_id: UUID, mode: SystemAlertMode) -> None:
        await self._client.preferences_set_mode(user_id, mode)

    async def set_custom_enabled(self, user_id: UUID, enabled: bool) -> None:
        await self._client.preferences_set_custom_enabled(user_id, enabled)
