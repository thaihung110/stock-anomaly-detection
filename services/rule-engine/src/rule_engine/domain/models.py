from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from rule_engine.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus


class UserAlertRule(BaseModel):
    rule_id: UUID | None = None  # None before INSERT; populated after
    user_id: UUID
    symbols: list[str]           # e.g. ["AAPL", "MSFT"] or ["*"] for all
    field: AlertField
    operator: AlertOperator
    threshold: float
    frequency: AlertFrequency = AlertFrequency.EVERY_TIME
    cooldown_min: int = 60
    status: AlertStatus = AlertStatus.ACTIVE
    # Joined from ``users.chat_id`` in ``get_active_rules``.
    # ``None`` means the user has not run /start yet — alert-service falls back
    # to the admin chat when enable_per_user_routing is True.
    chat_id: int | str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserAlertEvent(BaseModel):
    event_id: UUID | None = None  # None before INSERT; populated after
    rule_id: UUID
    user_id: UUID
    symbol: str
    triggered_at: datetime | None = None
    # Immutable snapshot at the moment the alert fires
    field_snapshot: AlertField
    operator_snapshot: AlertOperator
    threshold_snapshot: float
    triggered_value: float
