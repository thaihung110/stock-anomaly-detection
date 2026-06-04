import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator


class RuleName(str, Enum):
    """Rule identifiers used in AlertEvent.rule_name."""

    PRICE_ZSCORE = "price_zscore"
    VOLUME_ZSCORE = "volume_zscore"
    VOLUME_RATIO = "volume_ratio"
    BOLLINGER_BREAKOUT = "bollinger_breakout"
    RSI_EXTREME = "rsi_extreme"
    INTRADAY_RANGE = "intraday_range"


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class QuoteEvent(BaseModel):
    """Kafka message contract for raw.stock.quotes.

    Field names must exactly mirror yfinance-quotes-producer QuoteEvent.
    """

    symbol: str
    price: float
    change_pct: float
    day_volume: int
    day_high: float
    day_low: float
    prev_close: float
    event_ts: str  # ISO-8601 UTC e.g. "2026-05-14T10:23:15Z"

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()

    @field_validator("day_volume", mode="before")
    @classmethod
    def coerce_volume_to_int(cls, v: object) -> int:
        return int(v)


class AlertEvent(BaseModel):
    """Kafka message contract for alerts.raw topic (system rule violations)."""

    alert_id: str
    symbol: str
    event_ts: str
    rule_name: RuleName
    severity: AlertSeverity
    triggered_value: float
    threshold: float
    context_snapshot: dict[str, float]

    @staticmethod
    def build(
        quote: "QuoteEvent",
        rule_name: RuleName,
        severity: AlertSeverity,
        triggered_value: float,
        threshold: float,
        context_snapshot: dict[str, float],
    ) -> "AlertEvent":
        return AlertEvent(
            alert_id=str(uuid.uuid4()),
            symbol=quote.symbol,
            event_ts=quote.event_ts,
            rule_name=rule_name,
            severity=severity,
            triggered_value=triggered_value,
            threshold=threshold,
            context_snapshot=context_snapshot,
        )


class CustomAlertEvent(BaseModel):
    """Contract for alerts.user topic (custom user alert).

    Carries everything alert-service needs to route + format the message
    without querying the database. Mirror this schema in alert-service schema.py.
    """

    event_id: str      # UUID generated at fire time (for Kafka message deduplication)
    rule_id: str
    user_id: str
    chat_id: int | str | None   # joined from users; None → admin fallback
    symbol: str
    field: str         # AlertField value — alert-service uses to detect batch-daily fields
    operator: str      # AlertOperator value
    threshold: float
    triggered_value: float
    triggered_at: str  # ISO-8601 UTC

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()

    @staticmethod
    def build(
        rule: Any,  # UserAlertRule — Any avoids importing models into schema layer
        event_id: str,
        symbol: str,
        triggered_value: float,
        triggered_at: datetime,
    ) -> "CustomAlertEvent":
        return CustomAlertEvent(
            event_id=event_id,
            rule_id=str(rule.rule_id),
            user_id=str(rule.user_id),
            chat_id=rule.chat_id,
            symbol=symbol,
            field=rule.field.value,
            operator=rule.operator.value,
            threshold=rule.threshold,
            triggered_value=triggered_value,
            triggered_at=triggered_at.isoformat(),
        )


class ReloadResponse(BaseModel):
    status: str
    symbol_count: int
