"""LLM client factory — provider-agnostic via LangChain init_chat_model.

Supports any provider in the format "provider:model":
  google_genai:gemini-2.5-flash-lite
  openai:gpt-4o-mini
  anthropic:claude-haiku-4-5

The provider-specific package (langchain-google-genai, langchain-openai,
langchain-anthropic) must be installed separately as an optional extra.
Switching providers requires only the LLM_MODEL env change — no code edit.
"""
from typing import Any

from llm_agent.config import Settings
from llm_agent.llm.base import ClassifyResult


def build_llm(cfg: Settings) -> Any:
    """Return a runnable that accepts a prompt and returns ClassifyResult.

    Uses init_chat_model so provider routing is handled by LangChain.
    temperature=0 for deterministic classification output.
    """
    from langchain.chat_models import init_chat_model  # lazy — provider dep optional

    base_model = init_chat_model(cfg.llm_model, temperature=0)
    return base_model.with_structured_output(ClassifyResult)
