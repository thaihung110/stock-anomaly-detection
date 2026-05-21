"""Port interfaces — the hexagonal boundary between application and infrastructure.

Application layer depends only on these abstractions (DIP). Infrastructure
adapters implement them. This keeps domain/application pure and testable.
"""
from typing import Protocol
from uuid import UUID

from telegram_bot.domain.enums import AlertStatus
from telegram_bot.domain.models import UserAlertEvent, UserAlertRule


class IAlertRepository(Protocol):
    """Port: persistence operations for user alert rules and events."""

    async def get_or_create_user(self, telegram_id: int) -> UUID: ...

    async def insert_rule(self, rule: UserAlertRule) -> UUID: ...

    async def update_rule_status(self, rule_id: UUID, status: AlertStatus) -> None: ...

    async def get_rules_for_user(self, user_id: UUID) -> list[UserAlertRule]: ...

    async def get_alert_history(
        self, user_id: UUID, symbol: str | None = None
    ) -> list[UserAlertEvent]: ...

    async def delete_rule(self, rule_id: UUID, user_id: UUID) -> bool: ...

    async def rule_belongs_to_user(self, rule_id: UUID, user_id: UUID) -> bool: ...


class IRuleEngineClient(Protocol):
    """Port: hot-reload of user rules in the Rule Engine service."""

    async def reload_user_rules(self) -> bool: ...
