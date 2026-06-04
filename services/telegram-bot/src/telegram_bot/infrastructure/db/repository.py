"""UserAlertRepository — data access facade satisfying the IAlertRepository port.

Wraps DbClient so that application code (AlertService) depends only on the
port interface, not on asyncpg internals. Swap DbClient with a mock in tests.
"""
from uuid import UUID

import structlog

from telegram_bot.domain.enums import AlertStatus
from telegram_bot.domain.models import UserAlertEvent, UserAlertRule
from telegram_bot.infrastructure.db.client import DbClient

logger = structlog.get_logger(__name__)


class UserAlertRepository:
    def __init__(self, client: DbClient) -> None:
        self._client = client

    async def get_or_create_user(self, telegram_id: int) -> UUID:
        return await self._client.get_or_create_user(telegram_id)

    async def upsert_chat_id(self, telegram_id: int, chat_id: int) -> UUID:
        return await self._client.upsert_chat_id(telegram_id, chat_id)

    async def insert_rule(self, rule: UserAlertRule) -> UUID:
        return await self._client.insert_rule(rule)

    async def update_rule_status(self, rule_id: UUID, status: AlertStatus) -> None:
        await self._client.update_rule_status(rule_id, status)

    async def get_rules_for_user(self, user_id: UUID) -> list[UserAlertRule]:
        return await self._client.get_rules_for_user(user_id)

    async def get_alert_history(
        self, user_id: UUID, symbol: str | None = None
    ) -> list[UserAlertEvent]:
        return await self._client.get_alert_history(user_id, symbol)

    async def delete_rule(self, rule_id: UUID, user_id: UUID) -> bool:
        return await self._client.delete_rule(rule_id, user_id)

    async def rule_belongs_to_user(self, rule_id: UUID, user_id: UUID) -> bool:
        return await self._client.rule_belongs_to_user(rule_id, user_id)
