"""Tests for LLM abstraction layer — ClassifyResult schema and prompt formatting."""
from __future__ import annotations

import pytest

from llm_agent.llm.base import ClassifyResult
from llm_agent.llm.prompts import CLASSIFY_PROMPT, build_prompt_vars, format_news_text
from llm_agent.schema import AlertEvent, AlertSeverity, LLMJudgement, NewsCategory, RuleName


class TestClassifyResult:
    def test_explained_judgement(self) -> None:
        r = ClassifyResult(
            judgement=LLMJudgement.EXPLAINED,
            category=NewsCategory.EARNINGS,
            explanation="Strong earnings beat.",
            news_summary="AAPL Q2 beat estimates.",
            relevant_titles=["AAPL beats Q2"],
        )
        assert r.judgement == LLMJudgement.EXPLAINED
        assert r.category == NewsCategory.EARNINGS
        assert len(r.relevant_titles) == 1

    def test_unexplained_has_empty_defaults(self) -> None:
        r = ClassifyResult(judgement=LLMJudgement.UNEXPLAINED)
        assert r.category is None
        assert r.explanation is None
        assert r.news_summary is None
        assert r.relevant_titles == []

    def test_uncertain_accepted(self) -> None:
        r = ClassifyResult(judgement=LLMJudgement.UNCERTAIN)
        assert r.judgement == LLMJudgement.UNCERTAIN

    @pytest.mark.parametrize(
        "judgement",
        [LLMJudgement.EXPLAINED, LLMJudgement.UNEXPLAINED, LLMJudgement.UNCERTAIN],
    )
    def test_all_judgements_round_trip(self, judgement: LLMJudgement) -> None:
        r = ClassifyResult(judgement=judgement)
        restored = ClassifyResult.model_validate_json(r.model_dump_json())
        assert restored.judgement == judgement

    def test_relevant_titles_default_empty(self) -> None:
        assert ClassifyResult(judgement=LLMJudgement.UNEXPLAINED).relevant_titles == []


class TestFormatNewsText:
    def test_empty_list_returns_placeholder(self) -> None:
        text = format_news_text([])
        assert "no news articles found" in text

    def test_single_article(self) -> None:
        articles = [
            {"title": "AAPL Up 5%", "source": "Reuters", "published_at": "2026-06-01T14:00:00Z", "url": None}
        ]
        text = format_news_text(articles)
        assert "1." in text
        assert "AAPL Up 5%" in text
        assert "Reuters" in text

    def test_url_appended(self) -> None:
        articles = [
            {"title": "Story", "source": "BB", "published_at": "2026-06-01T10:00:00Z", "url": "http://bb.com/1"}
        ]
        text = format_news_text(articles)
        assert "http://bb.com/1" in text

    def test_no_url_no_dash(self) -> None:
        articles = [
            {"title": "Story", "source": "X", "published_at": "2026-06-01T10:00:00Z", "url": None}
        ]
        text = format_news_text(articles)
        assert "— http" not in text

    def test_multiple_articles_numbered(self) -> None:
        articles = [
            {"title": f"Story {i}", "source": "S", "published_at": "2026-06-01T10:00:00Z", "url": None}
            for i in range(3)
        ]
        text = format_news_text(articles)
        assert "1." in text
        assert "2." in text
        assert "3." in text

    def test_missing_source_no_empty_bracket(self) -> None:
        articles = [
            {"title": "Anon Story", "source": None, "published_at": "2026-06-01T10:00:00Z", "url": None}
        ]
        text = format_news_text(articles)
        assert "Anon Story" in text
        assert "[]" not in text

    def test_missing_title_shows_placeholder(self) -> None:
        articles = [
            {"title": None, "source": "X", "published_at": "2026-06-01T10:00:00Z", "url": None}
        ]
        text = format_news_text(articles)
        assert "(no title)" in text


class TestBuildPromptVars:
    def _alert(self) -> AlertEvent:
        return AlertEvent(
            alert_id="pv-001",
            symbol="AAPL",
            event_ts="2026-06-01T14:00:00Z",
            rule_name=RuleName.PRICE_ZSCORE,
            severity=AlertSeverity.HIGH,
            triggered_value=4.8,
            threshold=3.0,
            context_snapshot={"z_price": 4.8},
        )

    def test_contains_all_required_keys(self) -> None:
        alert = self._alert()
        articles: list = []
        vars_ = build_prompt_vars(alert, articles)
        required = {
            "symbol", "rule_name", "severity", "triggered_value", "threshold",
            "event_ts", "context_snapshot", "news_count", "news_text",
        }
        assert required == set(vars_.keys())

    def test_symbol_matches_alert(self) -> None:
        alert = self._alert()
        assert build_prompt_vars(alert, [])["symbol"] == "AAPL"

    def test_news_count_reflects_articles(self) -> None:
        alert = self._alert()
        articles = [{"title": "A"}, {"title": "B"}]
        assert build_prompt_vars(alert, articles)["news_count"] == "2"

    def test_empty_articles_gives_placeholder_news_text(self) -> None:
        alert = self._alert()
        text = build_prompt_vars(alert, [])["news_text"]
        assert "no news articles found" in text


class TestClassifyPrompt:
    def test_prompt_has_two_messages(self) -> None:
        messages = CLASSIFY_PROMPT.messages
        assert len(messages) == 2

    def test_prompt_contains_required_vars(self) -> None:
        required = {
            "symbol", "rule_name", "severity", "triggered_value", "threshold",
            "event_ts", "context_snapshot", "news_count", "news_text",
        }
        input_vars = set(CLASSIFY_PROMPT.input_variables)
        assert required == input_vars
