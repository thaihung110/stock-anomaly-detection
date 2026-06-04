"""User preference domain types — mirrors PostgreSQL ENUM system_alert_mode."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class SystemAlertMode(str, Enum):
    ALL = "ALL"
    WATCHLIST_ONLY = "WATCHLIST_ONLY"
    OFF = "OFF"


@dataclass(frozen=True)
class UserPreferences:
    user_id: UUID
    system_alert_mode: SystemAlertMode
    custom_alert_enabled: bool
