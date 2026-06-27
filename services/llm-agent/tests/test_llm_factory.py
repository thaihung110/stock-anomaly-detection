"""Tests for llm/factory.py — mocks init_chat_model to avoid real provider deps."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from llm_agent.config import Settings
from llm_agent.llm.base import ClassifyResult
from llm_agent.llm.factory import build_llm


def _cfg() -> Settings:
    return Settings(kafka_bootstrap_servers="localhost:9092")


class TestBuildLlm:
    @patch("langchain.chat_models.init_chat_model")
    def test_calls_init_chat_model_with_llm_model(self, mock_init: MagicMock) -> None:
        base = MagicMock()
        mock_init.return_value = base
        cfg = _cfg()
        build_llm(cfg)
        mock_init.assert_called_once_with(cfg.llm_model, temperature=0)

    @patch("langchain.chat_models.init_chat_model")
    def test_returns_structured_output_runnable(self, mock_init: MagicMock) -> None:
        base = MagicMock()
        structured = MagicMock()
        base.with_structured_output.return_value = structured
        mock_init.return_value = base
        result = build_llm(_cfg())
        base.with_structured_output.assert_called_once_with(ClassifyResult)
        assert result is structured

    @patch("langchain.chat_models.init_chat_model")
    def test_custom_model_string_passed_through(self, mock_init: MagicMock) -> None:
        base = MagicMock()
        mock_init.return_value = base
        cfg = Settings(kafka_bootstrap_servers="localhost:9092", llm_model="openai:gpt-4o-mini")
        build_llm(cfg)
        mock_init.assert_called_once_with("openai:gpt-4o-mini", temperature=0)
