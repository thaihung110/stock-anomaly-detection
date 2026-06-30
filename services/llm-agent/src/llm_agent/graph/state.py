"""LangGraph pipeline state for the anomaly classification agent.

AnomalyAgentState is the mutable context threaded through every node.
Fields are optional (total=False) because different nodes populate different
subsets — ingest sets alert, retrieve_news sets news_articles, classify
sets llm_judgement and related fields.
"""
from __future__ import annotations

from typing_extensions import TypedDict

from llm_agent.schema import AlertEvent, LLMJudgement, NewsCategory, NewsRef


class AnomalyAgentState(TypedDict, total=False):
    alert: AlertEvent
    news_articles: list[dict[str, str | None]]
    llm_judgement: LLMJudgement | None
    news_category: NewsCategory | None
    explanation: str | None
    news_summary: str | None
    news_refs: list[NewsRef]
    error: str | None
