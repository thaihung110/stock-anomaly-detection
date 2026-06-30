"""Kafka message contracts for the llm-agent service.

AlertEvent     — mirrors rule-engine; consumed from alerts.raw (never modified)
ConfirmedAlertEvent — superset of AlertEvent; published to alerts.confirmed
FollowUpEvent  — published to alerts.followup when re-check changes the verdict
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Covers US equities (AAPL), dual-class shares (BRK.A, BRK-B), and ETFs up to 5 chars.
# Rejects any injection attempt like "AAPL' OR '1'='1" before it reaches PyIceberg filters.
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


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
    """Mirror of rule-engine AlertEvent — contract for alerts.raw topic.

    Do NOT rename or add fields here. rule-engine is the source of truth.
    alert_id is minted once per rule-fire and carried through the entire pipeline.
    """

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
        v = v.upper()
        if not _SYMBOL_RE.match(v):
            raise ValueError(f"Invalid symbol format: {v!r}")
        return v


class ConfirmedAlertEvent(AlertEvent):
    """Published to alerts.confirmed after LLM classification.

    Inherits all AlertEvent fields so alert-service can parse it as AlertEvent
    for backward-compat (extra fields are ignored by default in Pydantic v2).
    """

    llm_judgement: LLMJudgement
    final_explanation: str | None = None
    news_summary: str | None = None
    news_category: NewsCategory | None = None
    news_refs: list[NewsRef] = Field(default_factory=list)
    agent_version: str = "1.0"


class FollowUpEvent(BaseModel):
    """Published to alerts.followup when re-check changes or confirms the verdict.

    Only emitted on FLIP (UNEXPLAINED->EXPLAINED or vice-versa) or CONFIRM.
    Never emitted when re-check finds nothing new — silence means "still waiting".
    """

    ref_alert_id: str
    symbol: str
    prev_judgement: LLMJudgement
    new_judgement: LLMJudgement
    news_summary: str | None = None
    news_refs: list[NewsRef] = Field(default_factory=list)
    emitted_at: str
    # Optional analytics fields — populated by recheck_queue (Stage D opt-in).
    # Carry the original alert's detection time and rule so anomaly_judgement
    # follow-up rows support time-to-explanation queries without a join.
    event_ts: str | None = None
    rule_name: RuleName | None = None

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        v = v.upper()
        if not _SYMBOL_RE.match(v):
            raise ValueError(f"Invalid symbol format: {v!r}")
        return v
