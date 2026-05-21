"""Repository for user alert rules and events — encapsulates all DB access."""
from uuid import UUID

import structlog

from rule_engine.infrastructure.db.client import DbClient
from rule_engine.domain.enums import AlertStatus
from rule_engine.domain.models import UserAlertEvent, UserAlertRule

logger = structlog.get_logger(__name__)


class UserAlertRepository:
    """Data access layer for user alert rules and events.

    Wraps DbClient to give business code a stable interface that can be
    mocked without coupling tests to asyncpg internals.
    """

    def __init__(self, client: DbClient) -> None:
        self._client = client

    async def get_active_rules(self) -> list[UserAlertRule]:
        return await self._client.get_active_rules()

    async def insert_event(self, event: UserAlertEvent) -> None:
        await self._client.insert_alert_event(event)

    async def mark_triggered(self, rule_id: UUID) -> None:
        await self._client.update_rule_status(rule_id, AlertStatus.TRIGGERED)
