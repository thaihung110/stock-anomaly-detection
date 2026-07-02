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


class LLMJudgement(str, Enum):
    EXPLAINED = "EXPLAINED"
    UNEXPLAINED = "UNEXPLAINED"
    UNCERTAIN = "UNCERTAIN"


class NewsCategory(str, Enum):
    EARNINGS = "EARNINGS"
    MACRO = "MACRO"
    REGULATORY = "REGULATORY"
    SECTOR = "SECTOR"
    CORPORATE = "CORPORATE"
    OTHER = "OTHER"


class NewsRef(BaseModel):
    title: str
    url: str | None = None
    published_at: str
    source: str | None = None


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


class ConfirmedAlertEvent(AlertEvent):
    """Mirror of llm-agent ConfirmedAlertEvent — contract for alerts.confirmed topic.

    Inherits AlertEvent so parsers expecting AlertEvent still work on this type.
    """

    llm_judgement: LLMJudgement
    final_explanation: str | None = None
    news_summary: str | None = None
    news_category: NewsCategory | None = None
    news_refs: list[NewsRef] = Field(default_factory=list)
    agent_version: str = "1.0"


class FollowUpEvent(BaseModel):
    """Mirror of llm-agent FollowUpEvent — contract for alerts.followup topic.

    Only emitted on FLIP or CONFIRM (never when re-check finds nothing new).
    """

    ref_alert_id: str
    symbol: str
    prev_judgement: LLMJudgement
    new_judgement: LLMJudgement
    news_summary: str | None = None
    news_refs: list[NewsRef] = Field(default_factory=list)
    emitted_at: str
    # Optional analytics fields — populated by llm-agent (Stage D opt-in).
    # Carry the original alert's detection time and rule so anomaly_judgement
    # follow-up rows support time-to-explanation queries without a join.
    event_ts: str | None = None
    rule_name: str | None = None

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class CustomAlertEvent(BaseModel):
    """Mirror of rule-engine CustomAlertEvent — contract for alerts.user topic."""

    event_id: str
    rule_id: str
    user_id: str
    chat_id: int | str | None
    symbol: str
    field: str         # AlertField value — used to detect batch-daily fields
    operator: str      # AlertOperator value
    threshold: float
    triggered_value: float
    triggered_at: str  # ISO-8601 UTC

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
