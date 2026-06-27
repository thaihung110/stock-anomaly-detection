"""LangGraph StateGraph assembly for the anomaly classification pipeline.

Stage B pipeline: ingest → retrieve_news → classify → END
Stage C pipeline: ingest → retrieve_news → classify → (UNEXPLAINED → schedule_recheck) | END

build_graph accepts a pre-built classify_chain (CLASSIFY_PROMPT | llm_client) so the
same chain can be reused by the recheck_queue worker without rebuilding it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from langgraph.graph import END, StateGraph

from llm_agent.graph.nodes import (
    make_classify_node,
    make_ingest_node,
    make_retrieve_news_node,
    make_schedule_recheck_node,
)
from llm_agent.graph.state import AnomalyAgentState
from llm_agent.schema import LLMJudgement

if TYPE_CHECKING:
    from llm_agent.infrastructure.circuit_breaker import CircuitBreaker
    from llm_agent.infrastructure.recheck_queue import RecheckQueue


def _route_after_classify(state: AnomalyAgentState) -> str:
    """Conditional edge: UNEXPLAINED → schedule_recheck, all others → END."""
    if state.get("llm_judgement") == LLMJudgement.UNEXPLAINED:
        return "schedule_recheck"
    return END


def build_graph(
    fetch_news: Callable[[str], list[dict[str, str | None]]],
    classify_chain: Any,
    *,
    recheck_queue: "RecheckQueue | None" = None,
    circuit_breaker: "CircuitBreaker | None" = None,
    recheck_delay_min: int = 20,
) -> Any:
    """Build and compile the LangGraph pipeline.

    Args:
        fetch_news: sync callable (symbol) → list[article_dict]; run in a thread.
        classify_chain: pre-built CLASSIFY_PROMPT | llm_client Runnable.
        recheck_queue: when provided, wires the UNEXPLAINED → schedule_recheck branch.
        circuit_breaker: when provided, injected into the classify node for fast-fail.
        recheck_delay_min: minutes after alert before the re-check fires.

    Returns:
        Compiled LangGraph graph ready for .ainvoke(state).
    """
    graph: StateGraph = StateGraph(AnomalyAgentState)

    graph.add_node("ingest", make_ingest_node())
    graph.add_node("retrieve_news", make_retrieve_news_node(fetch_news))
    graph.add_node(
        "classify",
        make_classify_node(classify_chain, circuit_breaker=circuit_breaker),
    )

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "retrieve_news")
    graph.add_edge("retrieve_news", "classify")

    if recheck_queue is not None:
        graph.add_node(
            "schedule_recheck",
            make_schedule_recheck_node(recheck_queue, recheck_delay_min),
        )
        graph.add_conditional_edges(
            "classify",
            _route_after_classify,
            {"schedule_recheck": "schedule_recheck", END: END},
        )
        graph.add_edge("schedule_recheck", END)
    else:
        graph.add_edge("classify", END)

    return graph.compile()
