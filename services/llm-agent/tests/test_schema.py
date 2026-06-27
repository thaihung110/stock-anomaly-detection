"""Round-trip serialisation tests for llm-agent Kafka contracts.

Verifies that ConfirmedAlertEvent and FollowUpEvent survive a full
JSON encode/decode cycle with all required and optional fields.
"""
from __future__ import annotations

import json

import pytest

from llm_agent.schema import (
    AlertSeverity,
    ConfirmedAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    NewsCategory,
    NewsRef,
    RuleName,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _base_alert_fields() -> dict:
    return {
        "alert_id": "a1b2c3d4-0000-0000-0000-000000000001",
        "symbol": "AAPL",
        "event_ts": "2026-06-01T14:00:00Z",
        "rule_name": RuleName.PRICE_ZSCORE,
        "severity": AlertSeverity.HIGH,
        "triggered_value": 155.5,
        "threshold": 150.0,
        "context_snapshot": {"z_price": 4.8, "price": 155.5},
    }


def _confirmed_event(**overrides: object) -> ConfirmedAlertEvent:
    return ConfirmedAlertEvent(
        **{**_base_alert_fields(), "llm_judgement": LLMJudgement.UNEXPLAINED, **overrides}
    )


def _followup_event(**overrides: object) -> FollowUpEvent:
    defaults: dict[str, object] = {
        "ref_alert_id": "a1b2c3d4-0000-0000-0000-000000000001",
        "symbol": "AAPL",
        "prev_judgement": LLMJudgement.UNEXPLAINED,
        "new_judgement": LLMJudgement.EXPLAINED,
        "emitted_at": "2026-06-01T14:20:00Z",
    }
    return FollowUpEvent(**{**defaults, **overrides})


# ── ConfirmedAlertEvent ───────────────────────────────────────────────────────


class TestConfirmedAlertEvent:
    def test_round_trip_minimal(self) -> None:
        event = _confirmed_event()
        payload = event.model_dump_json()
        restored = ConfirmedAlertEvent.model_validate_json(payload)
        assert restored == event

    def test_round_trip_with_all_fields(self) -> None:
        ref = NewsRef(
            title="Apple Earnings Beat",
            url="https://example.com/news/1",
            published_at="2026-06-01T10:00:00Z",
            source="Reuters",
        )
        event = _confirmed_event(
            llm_judgement=LLMJudgement.EXPLAINED,
            final_explanation="Strong earnings report drove the spike.",
            news_summary="Apple Q2 earnings exceeded expectations.",
            news_category=NewsCategory.EARNINGS,
            news_refs=[ref],
            agent_version="1.0",
        )
        payload = event.model_dump_json()
        restored = ConfirmedAlertEvent.model_validate_json(payload)
        assert restored.llm_judgement == LLMJudgement.EXPLAINED
        assert len(restored.news_refs) == 1
        assert restored.news_refs[0].title == "Apple Earnings Beat"
        assert restored.news_category == NewsCategory.EARNINGS

    def test_symbol_uppercased(self) -> None:
        event = _confirmed_event(symbol="aapl")
        assert event.symbol == "AAPL"

    def test_json_wire_format_contains_alert_id(self) -> None:
        event = _confirmed_event()
        wire = json.loads(event.model_dump_json())
        assert wire["alert_id"] == "a1b2c3d4-0000-0000-0000-000000000001"

    def test_uncertain_judgement_accepted(self) -> None:
        event = _confirmed_event(llm_judgement=LLMJudgement.UNCERTAIN)
        assert event.llm_judgement == LLMJudgement.UNCERTAIN

    def test_news_refs_default_empty(self) -> None:
        event = _confirmed_event()
        assert event.news_refs == []

    def test_inherits_alert_event_fields(self) -> None:
        event = _confirmed_event()
        assert event.alert_id == "a1b2c3d4-0000-0000-0000-000000000001"
        assert event.rule_name == RuleName.PRICE_ZSCORE
        assert event.severity == AlertSeverity.HIGH

    @pytest.mark.parametrize(
        "judgement",
        [LLMJudgement.EXPLAINED, LLMJudgement.UNEXPLAINED, LLMJudgement.UNCERTAIN],
    )
    def test_all_judgement_values_round_trip(self, judgement: LLMJudgement) -> None:
        event = _confirmed_event(llm_judgement=judgement)
        restored = ConfirmedAlertEvent.model_validate_json(event.model_dump_json())
        assert restored.llm_judgement == judgement


# ── FollowUpEvent ─────────────────────────────────────────────────────────────


class TestFollowUpEvent:
    def test_round_trip_minimal(self) -> None:
        event = _followup_event()
        payload = event.model_dump_json()
        restored = FollowUpEvent.model_validate_json(payload)
        assert restored == event

    def test_round_trip_with_news(self) -> None:
        ref = NewsRef(title="Late-breaking M&A deal", published_at="2026-06-01T14:15:00Z")
        event = _followup_event(
            news_summary="Acquisition announced 15 minutes after alert.",
            news_refs=[ref],
        )
        restored = FollowUpEvent.model_validate_json(event.model_dump_json())
        assert len(restored.news_refs) == 1
        assert restored.news_refs[0].title == "Late-breaking M&A deal"

    def test_symbol_uppercased(self) -> None:
        event = _followup_event(symbol="msft")
        assert event.symbol == "MSFT"

    def test_ref_alert_id_preserved(self) -> None:
        event = _followup_event()
        wire = json.loads(event.model_dump_json())
        assert wire["ref_alert_id"] == "a1b2c3d4-0000-0000-0000-000000000001"

    def test_flip_judgements(self) -> None:
        event = FollowUpEvent(
            ref_alert_id="x",
            symbol="TSLA",
            prev_judgement=LLMJudgement.UNEXPLAINED,
            new_judgement=LLMJudgement.EXPLAINED,
            emitted_at="2026-06-01T14:20:00Z",
        )
        assert event.prev_judgement != event.new_judgement

    def test_confirm_same_judgement(self) -> None:
        event = FollowUpEvent(
            ref_alert_id="x",
            symbol="TSLA",
            prev_judgement=LLMJudgement.UNEXPLAINED,
            new_judgement=LLMJudgement.UNEXPLAINED,
            emitted_at="2026-06-01T14:20:00Z",
        )
        assert event.prev_judgement == event.new_judgement

    def test_news_refs_default_empty(self) -> None:
        event = _followup_event()
        assert event.news_refs == []


# ── NewsRef ───────────────────────────────────────────────────────────────────


class TestNewsRef:
    def test_optional_fields_default_none(self) -> None:
        ref = NewsRef(title="Breaking News", published_at="2026-06-01T09:00:00Z")
        assert ref.url is None
        assert ref.source is None

    def test_round_trip_full(self) -> None:
        ref = NewsRef(
            title="SEC Filing",
            url="https://sec.gov/filing/123",
            published_at="2026-06-01T09:00:00Z",
            source="SEC",
        )
        restored = NewsRef.model_validate_json(ref.model_dump_json())
        assert restored == ref
