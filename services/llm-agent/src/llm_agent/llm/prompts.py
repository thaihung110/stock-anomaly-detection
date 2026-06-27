"""Prompt templates and prompt-variable helpers for LLM classification.

CLASSIFY_PROMPT: main classification prompt combining system instructions with
a human turn that includes the alert details and news articles.

format_news_text: converts article dicts into numbered plain text for the prompt.
build_prompt_vars: shared helper used by both the classify node and recheck_queue.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate

if TYPE_CHECKING:
    from llm_agent.schema import AlertEvent

_CLASSIFY_SYSTEM = """\
You are a financial analyst AI. Your task is to determine whether a stock market \
anomaly can be explained by public news.

Rules:
1. Mark EXPLAINED only when you find news that directly relates to the specific stock \
AND matches the direction and approximate magnitude of the anomaly.
2. Mark UNEXPLAINED when no news in the provided list explains the move. Do not invent reasons.
3. Mark UNCERTAIN only when you genuinely cannot determine (e.g., contradictory signals).
4. relevant_titles must contain verbatim titles from the provided list only — never \
fabricate titles.
5. If UNEXPLAINED, set relevant_titles to an empty list, category/explanation/news_summary to null.
6. news_summary must be derived from the actual articles; max 2 sentences.
"""

_CLASSIFY_HUMAN = """\
Stock anomaly alert:
  Symbol        : {symbol}
  Rule triggered: {rule_name}
  Severity      : {severity}
  Value / Thresh: {triggered_value} / {threshold}
  Event time    : {event_ts}
  Context       : {context_snapshot}

Recent news ({news_count} articles):
{news_text}

Classify this anomaly following the system rules above.
"""

CLASSIFY_PROMPT: ChatPromptTemplate = ChatPromptTemplate.from_messages(
    [
        ("system", _CLASSIFY_SYSTEM),
        ("human", _CLASSIFY_HUMAN),
    ]
)


def format_news_text(articles: list[dict[str, str | None]]) -> str:
    """Format article list as numbered plain text for the LLM prompt.

    Returns "(no news articles found)" when the list is empty so the model
    receives explicit signal rather than an empty section.
    """
    if not articles:
        return "(no news articles found)"

    lines: list[str] = []
    for i, art in enumerate(articles, 1):
        title = art.get("title") or "(no title)"
        source = art.get("source") or ""
        published = art.get("published_at") or ""
        url = art.get("url") or ""

        parts = [
            f"{i}.",
            f"[{source}]" if source else "",
            title,
            f"({published})" if published else "",
        ]
        line = " ".join(p for p in parts if p)
        if url:
            line += f" — {url}"
        lines.append(line)

    return "\n".join(lines)


def build_prompt_vars(
    alert: "AlertEvent",
    articles: list[dict[str, str | None]],
) -> dict[str, str]:
    """Build the template-variable dict for CLASSIFY_PROMPT.

    Shared between the classify node (Stage B) and the recheck worker (Stage C)
    so both paths produce identical prompts given the same alert + articles.
    """
    return {
        "symbol": alert.symbol,
        "rule_name": alert.rule_name.value,
        "severity": alert.severity.value,
        "triggered_value": str(alert.triggered_value),
        "threshold": str(alert.threshold),
        "event_ts": alert.event_ts,
        "context_snapshot": str(alert.context_snapshot),
        "news_count": str(len(articles)),
        "news_text": format_news_text(articles),
    }
