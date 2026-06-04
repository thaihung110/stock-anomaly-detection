"""Port interfaces — the hexagonal boundary between application and infrastructure.

Application layer depends only on these abstractions (DIP). Infrastructure
adapters implement them. This keeps domain/application pure and testable.
"""
from typing import Protocol
from uuid import UUID

from telegram_bot.domain.enums import AlertStatus
from telegram_bot.domain.models import UserAlertEvent, UserAlertRule
from telegram_bot.domain.preferences import SystemAlertMode, UserPreferences


class IAlertRepository(Protocol):
    """Port: persistence operations for user alert rules and events."""

    async def get_or_create_user(self, telegram_id: int) -> UUID: ...

    async def upsert_chat_id(self, telegram_id: int, chat_id: int) -> UUID: ...

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


class IAlertServiceClient(Protocol):
    """Port: subscriber-cache invalidation in the Alert Service."""

    async def reload_subscribers(self) -> bool: ...


class IWatchlistRepository(Protocol):
    """Port: per-user symbol watchlist persistence."""

    async def add(self, user_id: UUID, symbol: str) -> bool: ...

    async def remove(self, user_id: UUID, symbol: str) -> bool: ...

    async def list(self, user_id: UUID) -> list[str]: ...


class IPreferenceRepository(Protocol):
    """Port: per-user notification preferences."""

    async def get(self, user_id: UUID) -> UserPreferences: ...

    async def set_mode(self, user_id: UUID, mode: SystemAlertMode) -> None: ...

    async def set_custom_enabled(self, user_id: UUID, enabled: bool) -> None: ...
