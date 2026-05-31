from enum import Enum

from pydantic import BaseModel, Field, field_validator


class RuleName(str, Enum):
    PRICE_ZSCORE = "price_zscore"
    VOLUME_ZSCORE = "volume_zscore"
    VOLUME_RATIO = "volume_ratio"
    BOLLINGER_BREAKOUT = "bollinger_breakout"
    RSI_EXTREME = "rsi_extreme"
    INTRADAY_RANGE = "intraday_range"


class AlertSeverity(str, Enum):
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AlertEvent(BaseModel):
    """Mirror of rule-engine AlertEvent — contract for alerts.raw topic."""

    alert_id: str
    symbol: str
    event_ts: str
    rule_name: RuleName
    severity: AlertSeverity
    triggered_value: float
    threshold: float
    context_snapshot: dict[str, float]

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class DLQReason(str, Enum):
    """Why an alert was diverted to ``alerts.failed``."""

    RATE_LIMIT = "rate_limit"  # 429 after all retries
    PERMANENT = "permanent"  # 4xx that is not 429 (bad token, chat not found, ...)
    TRANSPORT = "transport"  # timeout / 5xx / network after all retries
    HISTORY_WRITE = "history_write"  # Telegram OK but Iceberg append failed
    SUBSCRIBER_LOOKUP = "subscriber_lookup"  # Postgres error fetching recipients


class FailedRecipient(BaseModel):
    """Subset of subscriber data preserved for DLQ replay."""

    user_id: str | None = None
    chat_id: int | str


class FailedAlertEnvelope(BaseModel):
    """Wire format for ``alerts.failed`` Kafka topic.

    Mirrors the shape consumed by the (future) replay job or operator tooling.
    ``failed_at_ms`` follows the project-wide epoch-milliseconds convention.
    """

    original_event: AlertEvent
    recipient: FailedRecipient | None
    reason: DLQReason
    error: str
    failed_at_ms: int = Field(..., ge=1_000_000_000_000)
    attempt_count: int = Field(..., ge=0)
