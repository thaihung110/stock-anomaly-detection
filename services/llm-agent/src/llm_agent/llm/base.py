"""Provider-agnostic structured output schema for LLM classification.

ClassifyResult is used with LangChain's `.with_structured_output(ClassifyResult)`
so the same schema works across Gemini, OpenAI, and Anthropic without any
provider-specific code.

relevant_titles is the relevance gate: the LLM must pick titles from the
articles it was given — preventing hallucinated news references.
"""
from pydantic import BaseModel, Field

from llm_agent.schema import LLMJudgement, NewsCategory


class ClassifyResult(BaseModel):
    """Structured output returned by the LLM for each alert."""

    judgement: LLMJudgement
    category: NewsCategory | None = None
    explanation: str | None = Field(
        default=None,
        description="Brief explanation of why the anomaly is EXPLAINED or UNEXPLAINED.",
    )
    news_summary: str | None = Field(
        default=None,
        description="1-2 sentence summary derived from the provided articles. Null if UNEXPLAINED.",
    )
    relevant_titles: list[str] = Field(
        default_factory=list,
        description=(
            "Titles (verbatim) from the provided articles that directly explain the anomaly. "
            "Empty list if UNEXPLAINED or no article passes the relevance gate."
        ),
    )
