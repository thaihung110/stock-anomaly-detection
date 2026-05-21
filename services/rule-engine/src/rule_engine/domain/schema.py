import uuid
from enum import Enum

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


class ReloadResponse(BaseModel):
    status: str
    symbol_count: int
