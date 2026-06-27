"""LangGraph node factories for the anomaly classification pipeline.

Each public function returns a callable suitable for StateGraph.add_node().
Using factory functions (closures) instead of bare functions lets us inject
dependencies (LLM chain, news fetcher, circuit breaker, recheck queue)
without global state.

Pipeline (Stage C):
  ingest → retrieve_news → classify → (UNEXPLAINED → schedule_recheck) | END
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

import structlog

from llm_agent.graph.state import AnomalyAgentState
from llm_agent.llm.prompts import build_prompt_vars
from llm_agent.metrics import CLASSIFY_LATENCY, NEWS_FETCHED
from llm_agent.schema import LLMJudgement, NewsRef

if TYPE_CHECKING:
    from llm_agent.infrastructure.circuit_breaker import CircuitBreaker
    from llm_agent.infrastructure.recheck_queue import RecheckQueue

logger = structlog.get_logger(__name__)


def make_ingest_node() -> Callable[[AnomalyAgentState], AnomalyAgentState]:
    """Return the ingest node: log alert receipt and initialise optional fields."""

    def ingest(state: AnomalyAgentState) -> AnomalyAgentState:
        alert = state["alert"]
        logger.info(
            "agent_ingest",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            rule=alert.rule_name.value,
            severity=alert.severity.value,
        )
        return {
            **state,
            "news_articles": [],
            "llm_judgement": None,
            "news_category": None,
            "explanation": None,
            "news_summary": None,
            "news_refs": [],
            "error": None,
        }

    return ingest


def make_retrieve_news_node(
    fetch_news: Callable[[str], list[dict[str, str | None]]],
) -> Callable[[AnomalyAgentState], Any]:
    """Return the retrieve_news node: fetch from 2 catalogs via fetch_news closure.

    fetch_news is a synchronous function (PyIceberg is sync) called in a
    thread executor so it does not block the asyncio event loop.
    """

    async def retrieve_news(state: AnomalyAgentState) -> AnomalyAgentState:
        symbol = state["alert"].symbol
        try:
            articles = await asyncio.to_thread(fetch_news, symbol)
        except Exception as exc:
            logger.warning("news_retrieval_failed", symbol=symbol, error=str(exc))
            articles = []
        NEWS_FETCHED.observe(len(articles))
        return {**state, "news_articles": articles}

    return retrieve_news


def make_classify_node(
    chain: Any,
    circuit_breaker: "CircuitBreaker | None" = None,
) -> Callable[[AnomalyAgentState], Any]:
    """Return the classify node: call LLM chain and apply relevance gate.

    chain is the pre-built CLASSIFY_PROMPT | llm_client (built in build.py).
    circuit_breaker, when provided, fast-fails to UNCERTAIN when OPEN — protecting
    the pipeline from cascading LLM failures.

    Relevance gate: only titles present in the retrieved articles are included
    in news_refs — the LLM cannot hallucinate references that were not given.
    """

    async def classify(state: AnomalyAgentState) -> AnomalyAgentState:
        alert = state["alert"]
        articles: list[dict[str, str | None]] = state.get("news_articles") or []

        # Circuit breaker: fast-fail if LLM has been erroring repeatedly
        if circuit_breaker is not None and circuit_breaker.is_open():
            logger.warning(
                "llm_circuit_open_fail_open",
                alert_id=alert.alert_id,
                cb_state=circuit_breaker.state.value,
            )
            return {
                **state,
                "llm_judgement": LLMJudgement.UNCERTAIN,
                "error": "circuit_open",
            }

        prompt_vars = build_prompt_vars(alert, articles)

        with CLASSIFY_LATENCY.time():
            try:
                result = await chain.ainvoke(prompt_vars)
                if circuit_breaker is not None:
                    circuit_breaker.record_success()
            except Exception as exc:
                if circuit_breaker is not None:
                    circuit_breaker.record_failure()
                logger.error(
                    "llm_classify_failed", alert_id=alert.alert_id, error=str(exc)
                )
                return {
                    **state,
                    "llm_judgement": LLMJudgement.UNCERTAIN,
                    "error": str(exc),
                }

        # Build news_refs — only from articles actually retrieved (relevance gate).
        # Walrus operator narrows type: t is str (never None) inside this comprehension.
        title_index: dict[str, dict[str, str | None]] = {
            t: a for a in articles if (t := a.get("title"))
        }
        news_refs: list[NewsRef] = [
            NewsRef(
                title=title,
                url=title_index[title].get("url"),
                published_at=title_index[title].get("published_at") or "",
                source=title_index[title].get("source"),
            )
            for title in result.relevant_titles
            if title in title_index
        ]

        logger.info(
            "llm_classified",
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            judgement=result.judgement.value,
            refs=len(news_refs),
        )
        return {
            **state,
            "llm_judgement": result.judgement,
            "news_category": result.category,
            "explanation": result.explanation,
            "news_summary": result.news_summary,
            "news_refs": news_refs,
        }

    return classify


def make_schedule_recheck_node(
    recheck_queue: "RecheckQueue",
    delay_min: int,
) -> Callable[[AnomalyAgentState], AnomalyAgentState]:
    """Return the schedule_recheck node: enqueue a follow-up for UNEXPLAINED alerts.

    The node is synchronous (no I/O) — it only pushes a task onto the in-memory
    queue.  The actual re-check runs asynchronously in a background worker.
    """

    def schedule_recheck(state: AnomalyAgentState) -> AnomalyAgentState:
        from llm_agent.infrastructure.recheck_queue import RecheckTask  # avoid circular

        alert = state["alert"]
        original_judgement = state.get("llm_judgement") or LLMJudgement.UNEXPLAINED
        recheck_at = datetime.now(tz=timezone.utc) + timedelta(minutes=delay_min)

        task = RecheckTask(
            alert_id=alert.alert_id,
            symbol=alert.symbol,
            original_judgement=original_judgement,
            recheck_at=recheck_at,
            alert=alert,
        )
        recheck_queue.enqueue_nowait(task)
        return state

    return schedule_recheck
