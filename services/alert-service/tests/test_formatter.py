"""Tests for alert-service formatter functions.

Covers both the existing format_message/format_custom_message and the new
format_confirmed_message/format_followup_message added in Stage A.
"""
from __future__ import annotations

import pytest

from alert_service.formatter import (
    format_confirmed_message,
    format_custom_message,
    format_followup_message,
    format_message,
)
from alert_service.schema import (
    AlertEvent,
    AlertSeverity,
    ConfirmedAlertEvent,
    CustomAlertEvent,
    FollowUpEvent,
    LLMJudgement,
    NewsRef,
    RuleName,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _alert(
    rule: RuleName = RuleName.PRICE_ZSCORE,
    severity: AlertSeverity = AlertSeverity.HIGH,
    symbol: str = "AAPL",
) -> AlertEvent:
    return AlertEvent(
        alert_id="test-id-001",
        symbol=symbol,
        event_ts="2026-06-01T14:00:00Z",
        rule_name=rule,
        severity=severity,
        triggered_value=155.5,
        threshold=150.0,
        context_snapshot={"z_price": 4.8},
    )


def _confirmed(
    judgement: LLMJudgement = LLMJudgement.UNEXPLAINED,
    rule: RuleName = RuleName.PRICE_ZSCORE,
    severity: AlertSeverity = AlertSeverity.HIGH,
    **overrides: object,
) -> ConfirmedAlertEvent:
    return ConfirmedAlertEvent(
        alert_id="test-id-001",
        symbol="AAPL",
        event_ts="2026-06-01T14:00:00Z",
        rule_name=rule,
        severity=severity,
        triggered_value=155.5,
        threshold=150.0,
        context_snapshot={"z_price": 4.8},
        llm_judgement=judgement,
        **overrides,
    )


def _followup(
    prev: LLMJudgement = LLMJudgement.UNEXPLAINED,
    new: LLMJudgement = LLMJudgement.EXPLAINED,
    **overrides: object,
) -> FollowUpEvent:
    return FollowUpEvent(
        ref_alert_id="test-id-001",
        symbol="AAPL",
        prev_judgement=prev,
        new_judgement=new,
        emitted_at="2026-06-01T14:20:00Z",
        **overrides,
    )


# ── format_message (backward-compat) ─────────────────────────────────────────


class TestFormatMessage:
    def test_contains_symbol_and_severity(self) -> None:
        text = format_message(_alert())
        assert "AAPL" in text
        assert "HIGH" in text

    def test_rsi_extreme_includes_batch_note(self) -> None:
        text = format_message(_alert(rule=RuleName.RSI_EXTREME))
        assert "batch" in text.lower() or "end-of-previous-day" in text.lower()

    def test_bollinger_includes_batch_note(self) -> None:
        text = format_message(_alert(rule=RuleName.BOLLINGER_BREAKOUT))
        assert "batch" in text.lower() or "end-of-previous-day" in text.lower()

    def test_price_zscore_no_batch_note(self) -> None:
        text = format_message(_alert(rule=RuleName.PRICE_ZSCORE))
        assert "end-of-previous-day" not in text

    def test_medium_severity_emoji(self) -> None:
        text = format_message(_alert(severity=AlertSeverity.MEDIUM))
        assert "MEDIUM" in text


# ── format_confirmed_message ─────────────────────────────────────────────────


class TestFormatConfirmedMessage:
    def test_unexplained_verdict_in_output(self) -> None:
        text = format_confirmed_message(_confirmed(LLMJudgement.UNEXPLAINED))
        assert "UNEXPLAINED" in text

    def test_explained_verdict_in_output(self) -> None:
        text = format_confirmed_message(_confirmed(LLMJudgement.EXPLAINED))
        assert "EXPLAINED" in text

    def test_uncertain_verdict_in_output(self) -> None:
        text = format_confirmed_message(_confirmed(LLMJudgement.UNCERTAIN))
        assert "UNCERTAIN" in text

    def test_contains_symbol_and_rule(self) -> None:
        text = format_confirmed_message(_confirmed())
        assert "AAPL" in text
        assert "Price Zscore" in text or "price_zscore" in text.lower()

    def test_final_explanation_included(self) -> None:
        text = format_confirmed_message(
            _confirmed(final_explanation="Strong Q2 earnings report caused the spike.")
        )
        assert "Strong Q2 earnings" in text

    def test_news_refs_top_3_shown(self) -> None:
        refs = [
            NewsRef(title=f"Story {i}", published_at="2026-06-01T10:00:00Z")
            for i in range(5)
        ]
        text = format_confirmed_message(_confirmed(news_refs=refs))
        assert "Story 0" in text
        assert "Story 2" in text
        assert "Story 4" not in text  # only top 3

    def test_no_news_refs_no_related_block(self) -> None:
        text = format_confirmed_message(_confirmed())
        assert "Related news" not in text

    def test_news_ref_with_source_shows_source(self) -> None:
        ref = NewsRef(title="Q2 Beat", published_at="2026-06-01T10:00:00Z", source="Reuters")
        text = format_confirmed_message(_confirmed(news_refs=[ref]))
        assert "Reuters" in text

    def test_rsi_extreme_includes_batch_note(self) -> None:
        text = format_confirmed_message(_confirmed(rule=RuleName.RSI_EXTREME))
        assert "end-of-previous-day" in text or "batch" in text.lower()

    def test_price_zscore_no_batch_note(self) -> None:
        text = format_confirmed_message(_confirmed(rule=RuleName.PRICE_ZSCORE))
        assert "end-of-previous-day" not in text

    def test_no_markdown_v2_special_chars(self) -> None:
        text = format_confirmed_message(
            _confirmed(final_explanation="Revenue up 15%, beats by $0.50")
        )
        assert "\\" not in text


# ── format_followup_message ──────────────────────────────────────────────────


class TestFormatFollowupMessage:
    def test_flip_label_shown(self) -> None:
        text = format_followup_message(
            _followup(LLMJudgement.UNEXPLAINED, LLMJudgement.EXPLAINED)
        )
        assert "VERDICT CHANGED" in text or "UNEXPLAINED" in text

    def test_confirm_label_shown(self) -> None:
        text = format_followup_message(
            _followup(LLMJudgement.UNEXPLAINED, LLMJudgement.UNEXPLAINED)
        )
        assert "CONFIRMED" in text

    def test_symbol_in_header(self) -> None:
        text = format_followup_message(_followup())
        assert "AAPL" in text

    def test_news_summary_included(self) -> None:
        text = format_followup_message(
            _followup(news_summary="CEO resignation announced after market close.")
        )
        assert "CEO resignation" in text

    def test_news_refs_top_3_shown(self) -> None:
        refs = [
            NewsRef(title=f"Breaking {i}", published_at="2026-06-01T14:15:00Z")
            for i in range(4)
        ]
        text = format_followup_message(_followup(news_refs=refs))
        assert "Breaking 0" in text
        assert "Breaking 2" in text
        assert "Breaking 3" not in text

    def test_no_news_refs_no_evidence_block(self) -> None:
        text = format_followup_message(_followup())
        assert "New evidence" not in text

    def test_no_news_summary_no_none_text(self) -> None:
        text = format_followup_message(_followup())
        assert "None" not in text


# ── format_custom_message (backward-compat) ──────────────────────────────────


class TestFormatCustomMessage:
    def test_contains_symbol_and_field(self) -> None:
        event = CustomAlertEvent(
            event_id="e1",
            rule_id="r1",
            user_id="u1",
            chat_id=12345,
            symbol="TSLA",
            field="price",
            operator=">",
            threshold=250.0,
            triggered_value=260.0,
            triggered_at="2026-06-01T14:00:00Z",
        )
        text = format_custom_message(event)
        assert "TSLA" in text
        assert "price" in text

    def test_batch_field_includes_note(self) -> None:
        event = CustomAlertEvent(
            event_id="e2",
            rule_id="r2",
            user_id="u2",
            chat_id=12345,
            symbol="AAPL",
            field="rsi_14",
            operator=">",
            threshold=80.0,
            triggered_value=82.0,
            triggered_at="2026-06-01T14:00:00Z",
        )
        text = format_custom_message(event)
        assert "end-of-previous-day" in text
