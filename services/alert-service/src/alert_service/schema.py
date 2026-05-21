from enum import Enum

from pydantic import BaseModel, field_validator


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
