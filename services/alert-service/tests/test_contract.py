"""Contract tests: llm-agent JSON wire format ↔ alert-service schema parsing.

Verifies that ConfirmedAlertEvent and FollowUpEvent serialized by the llm-agent
can be correctly parsed by alert-service without field loss or type errors.

These are the "seam" tests for the alerts.confirmed / alerts.followup Kafka topics.
If either of these tests fails, the cutover in Bước 11 will break user delivery.
"""
from __future__ import annotations

import json

import pytest

from alert_service.core.schema import (
    AlertSeverity,
    ConfirmedAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    NewsCategory,
    RuleName,
)


# ── Minimal wire payloads matching llm-agent schema output ────────────────────
# These dicts represent what llm-agent publishes to Kafka (model_dump_json output).
# Field set must stay in sync with llm_agent.schema.ConfirmedAlertEvent /
# llm_agent.schema.FollowUpEvent.  Update these when either schema changes.


def _confirmed_wire(
    judgement: str = "EXPLAINED",
    category: str | None = "EARNINGS",
    explanation: str | None = "Strong Q2 earnings beat.",
    news_refs: list[dict] | None = None,
) -> str:
    """Simulate the JSON payload published by llm-agent to alerts.confirmed."""
    payload: dict = {
        # AlertEvent base fields (rule-engine wire format — never change)
        "alert_id": "a1b2c3d4-0000-0000-0000-000000000001",
        "symbol": "AAPL",
        "event_ts": "2026-06-01T14:00:00Z",
        "rule_name": "price_zscore",
        "severity": "HIGH",
        "triggered_value": 4.8,
        "threshold": 3.0,
        "context_snapshot": {"z_price": 4.8, "price": 155.5},
        # ConfirmedAlertEvent extensions added by llm-agent
        "llm_judgement": judgement,
        "final_explanation": explanation,
        "news_summary": "AAPL Q2 EPS beat estimates by $0.12.",
        "news_category": category,
        "news_refs": news_refs
        if news_refs is not None
        else [
            {
                "title": "Apple Q2 Results",
                "url": "https://example.com/aapl-q2",
                "published_at": "2026-06-01T12:00:00Z",
                "source": "Reuters",
            }
        ],
        "agent_version": "1.0",
    }
    return json.dumps(payload)


def _followup_wire(
    prev: str = "UNEXPLAINED",
    new: str = "EXPLAINED",
    event_ts: str | None = "2026-06-01T14:00:00Z",
    rule_name: str | None = "volume_zscore",
) -> str:
    """Simulate the JSON payload published by llm-agent to alerts.followup."""
    payload: dict = {
        "ref_alert_id": "a1b2c3d4-0000-0000-0000-000000000001",
        "symbol": "NVDA",
        "prev_judgement": prev,
        "new_judgement": new,
        "news_summary": "Morgan Stanley upgraded NVDA after-hours.",
        "news_refs": [
            {
                "title": "NVDA Upgrade",
                "url": "https://example.com/nvda",
                "published_at": "2026-06-01T14:45:00Z",
                "source": "CNBC",
            }
        ],
        "emitted_at": "2026-06-01T14:52:00Z",
        "event_ts": event_ts,
        "rule_name": rule_name,
    }
    return json.dumps(payload)


# ── ConfirmedAlertEvent contract ──────────────────────────────────────────────


class TestConfirmedAlertEventContract:
    def test_parses_explained_payload(self) -> None:
        event = ConfirmedAlertEvent.model_validate_json(_confirmed_wire())
        assert event.alert_id == "a1b2c3d4-0000-0000-0000-000000000001"
        assert event.symbol == "AAPL"
        assert event.llm_judgement == LLMJudgement.EXPLAINED

    def test_all_base_alert_fields_preserved(self) -> None:
        event = ConfirmedAlertEvent.model_validate_json(_confirmed_wire())
        assert event.rule_name == RuleName.PRICE_ZSCORE
        assert event.severity == AlertSeverity.HIGH
        assert event.triggered_value == pytest.approx(4.8)
        assert event.threshold == pytest.approx(3.0)
        assert event.context_snapshot == {"z_price": 4.8, "price": 155.5}

    def test_news_category_parsed(self) -> None:
        event = ConfirmedAlertEvent.model_validate_json(
            _confirmed_wire(category="EARNINGS")
        )
        assert event.news_category == NewsCategory.EARNINGS

    def test_news_refs_parsed(self) -> None:
        event = ConfirmedAlertEvent.model_validate_json(_confirmed_wire())
        assert len(event.news_refs) == 1
        assert event.news_refs[0].title == "Apple Q2 Results"
        assert event.news_refs[0].source == "Reuters"

    def test_unexplained_no_category(self) -> None:
        wire = _confirmed_wire(judgement="UNEXPLAINED", category=None,
                               explanation=None)
        event = ConfirmedAlertEvent.model_validate_json(wire)
        assert event.llm_judgement == LLMJudgement.UNEXPLAINED
        assert event.news_category is None
        assert event.final_explanation is None

    def test_uncertain_judgement_accepted(self) -> None:
        wire = _confirmed_wire(judgement="UNCERTAIN", category=None,
                               explanation=None, news_refs=[])
        event = ConfirmedAlertEvent.model_validate_json(wire)
        assert event.llm_judgement == LLMJudgement.UNCERTAIN
        assert event.news_refs == []

    @pytest.mark.parametrize("judgement", ["EXPLAINED", "UNEXPLAINED", "UNCERTAIN"])
    def test_all_judgement_values_accepted(self, judgement: str) -> None:
        wire = _confirmed_wire(judgement=judgement, category=None,
                               explanation=None, news_refs=[])
        event = ConfirmedAlertEvent.model_validate_json(wire)
        assert event.llm_judgement.value == judgement

    def test_symbol_uppercase_enforced(self) -> None:
        data = json.loads(_confirmed_wire())
        data["symbol"] = "aapl"
        event = ConfirmedAlertEvent.model_validate(data)
        assert event.symbol == "AAPL"

    def test_extra_future_fields_ignored(self) -> None:
        """Forward-compat: a newer llm-agent with extra fields must not crash."""
        data = json.loads(_confirmed_wire())
        data["future_field_v4"] = "some_value"
        event = ConfirmedAlertEvent.model_validate(data)
        assert event.alert_id == "a1b2c3d4-0000-0000-0000-000000000001"

    def test_round_trip_serialize_deserialize(self) -> None:
        """Parsed event can be re-serialized and re-parsed identically."""
        wire = _confirmed_wire()
        e1 = ConfirmedAlertEvent.model_validate_json(wire)
        e2 = ConfirmedAlertEvent.model_validate_json(e1.model_dump_json())
        assert e1 == e2

    @pytest.mark.parametrize("category", ["EARNINGS", "MACRO", "REGULATORY",
                                           "SECTOR", "CORPORATE", "OTHER"])
    def test_all_news_categories_accepted(self, category: str) -> None:
        wire = _confirmed_wire(category=category)
        event = ConfirmedAlertEvent.model_validate_json(wire)
        assert event.news_category is not None
        assert event.news_category.value == category


# ── FollowUpEvent contract ────────────────────────────────────────────────────


class TestFollowUpEventContract:
    def test_parses_flip_payload(self) -> None:
        wire = _followup_wire(prev="UNEXPLAINED", new="EXPLAINED")
        event = FollowUpEvent.model_validate_json(wire)
        assert event.ref_alert_id == "a1b2c3d4-0000-0000-0000-000000000001"
        assert event.prev_judgement == LLMJudgement.UNEXPLAINED
        assert event.new_judgement == LLMJudgement.EXPLAINED

    def test_parses_confirm_payload(self) -> None:
        wire = _followup_wire(prev="UNEXPLAINED", new="UNEXPLAINED")
        event = FollowUpEvent.model_validate_json(wire)
        assert event.prev_judgement == event.new_judgement == LLMJudgement.UNEXPLAINED

    def test_symbol_preserved(self) -> None:
        assert FollowUpEvent.model_validate_json(_followup_wire()).symbol == "NVDA"

    def test_news_refs_parsed(self) -> None:
        event = FollowUpEvent.model_validate_json(_followup_wire())
        assert len(event.news_refs) == 1
        assert event.news_refs[0].source == "CNBC"

    def test_analytics_fields_populated(self) -> None:
        """Stage D: event_ts and rule_name carry original alert context."""
        event = FollowUpEvent.model_validate_json(
            _followup_wire(event_ts="2026-06-01T14:00:00Z", rule_name="volume_zscore")
        )
        assert event.event_ts == "2026-06-01T14:00:00Z"
        assert event.rule_name == "volume_zscore"

    def test_analytics_fields_optional_absent(self) -> None:
        """Legacy llm-agent without Stage D fields: must still parse cleanly."""
        data = json.loads(_followup_wire())
        data.pop("event_ts", None)
        data.pop("rule_name", None)
        event = FollowUpEvent.model_validate(data)
        assert event.event_ts is None
        assert event.rule_name is None

    def test_analytics_fields_optional_null(self) -> None:
        """llm-agent sends null when analytics fields not available."""
        wire = _followup_wire(event_ts=None, rule_name=None)
        event = FollowUpEvent.model_validate_json(wire)
        assert event.event_ts is None
        assert event.rule_name is None

    def test_extra_future_fields_ignored(self) -> None:
        data = json.loads(_followup_wire())
        data["new_field_v3"] = True
        event = FollowUpEvent.model_validate(data)
        assert event.ref_alert_id == "a1b2c3d4-0000-0000-0000-000000000001"

    def test_symbol_uppercase_enforced(self) -> None:
        data = json.loads(_followup_wire())
        data["symbol"] = "nvda"
        assert FollowUpEvent.model_validate(data).symbol == "NVDA"

    def test_round_trip(self) -> None:
        wire = _followup_wire()
        e1 = FollowUpEvent.model_validate_json(wire)
        e2 = FollowUpEvent.model_validate_json(e1.model_dump_json())
        assert e1 == e2
