"""Provider-agnostic tests: verify build_llm works with multiple LLM_MODEL strings.

The design goal is that changing LLM_MODEL="google_genai:gemini-2.5-flash-lite"
to "openai:gpt-4o-mini" or "anthropic:claude-haiku-4-5" requires zero code changes.

These tests verify:
1. build_llm correctly routes to init_chat_model with the given model string.
2. The structured output wrapper is applied to ClassifyResult for all providers.
3. Switching providers only requires changing one Settings field.
4. The classify pipeline produces identical results regardless of provider (mocked).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_agent.config import Settings
from llm_agent.llm.base import ClassifyResult
from llm_agent.llm.factory import build_llm
from llm_agent.schema import LLMJudgement


def _cfg(model: str) -> Settings:
    return Settings(kafka_bootstrap_servers="localhost:9092", llm_model=model)


# ── Provider routing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_string",
    [
        "google_genai:gemini-2.5-flash-lite",
        "openai:gpt-4o-mini",
        "anthropic:claude-haiku-4-5",
    ],
)
class TestProviderRouting:
    @patch("langchain.chat_models.init_chat_model")
    def test_init_chat_model_called_with_model_string(
        self, mock_init: MagicMock, model_string: str
    ) -> None:
        base = MagicMock()
        mock_init.return_value = base
        build_llm(_cfg(model_string))
        mock_init.assert_called_once_with(model_string, temperature=0)

    @patch("langchain.chat_models.init_chat_model")
    def test_structured_output_applied(
        self, mock_init: MagicMock, model_string: str
    ) -> None:
        base = MagicMock()
        mock_init.return_value = base
        build_llm(_cfg(model_string))
        base.with_structured_output.assert_called_once_with(ClassifyResult)

    @patch("langchain.chat_models.init_chat_model")
    def test_returns_structured_runnable(
        self, mock_init: MagicMock, model_string: str
    ) -> None:
        structured = MagicMock()
        base = MagicMock()
        base.with_structured_output.return_value = structured
        mock_init.return_value = base
        assert build_llm(_cfg(model_string)) is structured

    @patch("langchain.chat_models.init_chat_model")
    def test_temperature_is_zero(
        self, mock_init: MagicMock, model_string: str
    ) -> None:
        """Deterministic classification requires temperature=0 for all providers."""
        base = MagicMock()
        mock_init.return_value = base
        build_llm(_cfg(model_string))
        _, kwargs = mock_init.call_args
        assert kwargs.get("temperature") == 0


# ── Classify pipeline works regardless of provider ────────────────────────────


@pytest.mark.parametrize(
    "model_string",
    [
        "google_genai:gemini-2.5-flash-lite",
        "openai:gpt-4o-mini",
        "anthropic:claude-haiku-4-5",
    ],
)
class TestClassifyPipelineProviderAgnostic:
    async def test_pipeline_returns_judgement(self, model_string: str) -> None:
        """With any provider (mocked), the graph returns a valid ClassifyResult."""
        from llm_agent.graph.build import build_graph
        from llm_agent.schema import AlertEvent, AlertSeverity, RuleName

        alert = AlertEvent(
            alert_id="prov-test-001",
            symbol="AAPL",
            event_ts="2026-06-01T14:00:00Z",
            rule_name=RuleName.PRICE_ZSCORE,
            severity=AlertSeverity.HIGH,
            triggered_value=4.8,
            threshold=3.0,
            context_snapshot={"z_price": 4.8},
        )
        classify_result = ClassifyResult(
            judgement=LLMJudgement.EXPLAINED,
            relevant_titles=[],
        )
        chain_mock = AsyncMock()
        chain_mock.ainvoke = AsyncMock(return_value=classify_result)

        graph = build_graph(
            fetch_news=MagicMock(return_value=[]),
            classify_chain=chain_mock,
        )
        result = await graph.ainvoke({"alert": alert})
        assert result["llm_judgement"] == LLMJudgement.EXPLAINED

    @patch("langchain.chat_models.init_chat_model")
    def test_switching_provider_requires_no_code_change(
        self, mock_init: MagicMock, model_string: str
    ) -> None:
        """Provider switch = change LLM_MODEL env only. Same code path for all."""
        base = MagicMock()
        mock_init.return_value = base
        build_llm(_cfg(model_string))
        mock_init.assert_called_once_with(model_string, temperature=0)


# ── LLM config ───────────────────────────────────────────────────────────────


class TestLLMConfig:
    def test_default_model_is_gemini(self) -> None:
        cfg = Settings(kafka_bootstrap_servers="localhost:9092")
        assert cfg.llm_model == "google_genai:gemini-2.5-flash-lite"

    def test_model_changeable_via_settings(self) -> None:
        cfg = Settings(
            kafka_bootstrap_servers="localhost:9092",
            llm_model="openai:gpt-4o-mini",
        )
        assert cfg.llm_model == "openai:gpt-4o-mini"

    def test_empty_escalation_model_is_default(self) -> None:
        assert Settings(kafka_bootstrap_servers="localhost:9092").llm_escalation_model == ""
