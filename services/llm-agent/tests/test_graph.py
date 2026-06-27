"""Tests for LangGraph pipeline nodes, routing, and build.

All LLM calls and news fetches are mocked — no live services needed.
Stage C additions: circuit breaker in classify, schedule_recheck node, conditional edge.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from llm_agent.graph.build import _route_after_classify, build_graph
from llm_agent.graph.nodes import (
    make_classify_node,
    make_ingest_node,
    make_retrieve_news_node,
    make_schedule_recheck_node,
)
from llm_agent.graph.state import AnomalyAgentState
from llm_agent.infrastructure.circuit_breaker import CircuitBreaker
from llm_agent.infrastructure.recheck_queue import RecheckQueue
from llm_agent.llm.base import ClassifyResult
from llm_agent.schema import (
    AlertEvent,
    AlertSeverity,
    LLMJudgement,
    NewsCategory,
    RuleName,
)


def _alert(**overrides: object) -> AlertEvent:
    defaults = {
        "alert_id": "test-001",
        "symbol": "AAPL",
        "event_ts": "2026-06-01T14:00:00Z",
        "rule_name": RuleName.PRICE_ZSCORE,
        "severity": AlertSeverity.HIGH,
        "triggered_value": 4.8,
        "threshold": 3.0,
        "context_snapshot": {"z_price": 4.8},
    }
    return AlertEvent(**{**defaults, **overrides})  # type: ignore[arg-type]


def _base_state(alert: AlertEvent | None = None) -> AnomalyAgentState:
    return {"alert": alert or _alert()}  # type: ignore[return-value]


def _mock_chain(
    judgement: LLMJudgement,
    titles: list[str] | None = None,
    category: NewsCategory | None = None,
) -> AsyncMock:
    result = ClassifyResult(
        judgement=judgement,
        category=category or (NewsCategory.EARNINGS if judgement == LLMJudgement.EXPLAINED else None),
        explanation="Test explanation" if judgement == LLMJudgement.EXPLAINED else None,
        news_summary="Test summary" if judgement == LLMJudgement.EXPLAINED else None,
        relevant_titles=titles or [],
    )
    chain = AsyncMock()
    chain.ainvoke = AsyncMock(return_value=result)
    return chain


# ── Ingest ───────────────────────────────────────────────────────────────────


class TestIngestNode:
    def test_initialises_all_optional_fields(self) -> None:
        node = make_ingest_node()
        result = node(_base_state())
        assert result["news_articles"] == []
        assert result["llm_judgement"] is None
        assert result["news_category"] is None
        assert result["explanation"] is None
        assert result["news_summary"] is None
        assert result["news_refs"] == []
        assert result["error"] is None

    def test_preserves_alert_identity(self) -> None:
        node = make_ingest_node()
        alert = _alert(symbol="MSFT")
        result = node({"alert": alert})  # type: ignore[typeddict-item]
        assert result["alert"].symbol == "MSFT"


# ── Retrieve news ─────────────────────────────────────────────────────────────


class TestRetrieveNewsNode:
    async def test_passes_articles_to_state(self) -> None:
        articles = [{"title": "News", "url": None, "source": "X", "published_at": "2026-06-01"}]
        fetch_fn = MagicMock(return_value=articles)
        node = make_retrieve_news_node(fetch_fn)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        result = await node(state)
        assert result["news_articles"] == articles
        fetch_fn.assert_called_once_with("AAPL")

    async def test_exception_returns_empty_articles(self) -> None:
        fetch_fn = MagicMock(side_effect=ConnectionError("unreachable"))
        node = make_retrieve_news_node(fetch_fn)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        result = await node(state)
        assert result["news_articles"] == []


# ── Classify ─────────────────────────────────────────────────────────────────


class TestClassifyNode:
    async def test_explained_populates_all_fields(self) -> None:
        articles = [{"title": "Earnings Beat", "url": None, "source": "Reuters", "published_at": "2026-06-01T10:00:00Z"}]
        chain = _mock_chain(LLMJudgement.EXPLAINED, titles=["Earnings Beat"])
        node = make_classify_node(chain)
        state: AnomalyAgentState = {**_base_state(), "news_articles": articles}  # type: ignore[assignment]
        result = await node(state)
        assert result["llm_judgement"] == LLMJudgement.EXPLAINED
        assert result["news_category"] == NewsCategory.EARNINGS
        assert len(result.get("news_refs", [])) == 1
        assert result["news_refs"][0].title == "Earnings Beat"

    async def test_unexplained_has_no_refs(self) -> None:
        articles = [{"title": "Unrelated", "url": None, "source": "X", "published_at": "2026-06-01T10:00:00Z"}]
        chain = _mock_chain(LLMJudgement.UNEXPLAINED, titles=[])
        node = make_classify_node(chain)
        state: AnomalyAgentState = {**_base_state(), "news_articles": articles}  # type: ignore[assignment]
        result = await node(state)
        assert result["llm_judgement"] == LLMJudgement.UNEXPLAINED
        assert result.get("news_refs") == []

    async def test_relevance_gate_blocks_hallucinated_title(self) -> None:
        articles = [{"title": "Real News", "url": None, "source": "X", "published_at": "2026-06-01T10:00:00Z"}]
        chain = _mock_chain(LLMJudgement.EXPLAINED, titles=["HALLUCINATED TITLE"])
        node = make_classify_node(chain)
        state: AnomalyAgentState = {**_base_state(), "news_articles": articles}  # type: ignore[assignment]
        result = await node(state)
        assert result.get("news_refs", []) == []

    async def test_llm_exception_returns_uncertain(self) -> None:
        chain = AsyncMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("API error"))
        node = make_classify_node(chain)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        result = await node(state)
        assert result["llm_judgement"] == LLMJudgement.UNCERTAIN
        assert result.get("error") is not None

    async def test_empty_articles_still_calls_llm(self) -> None:
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        node = make_classify_node(chain)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        result = await node(state)
        assert result["llm_judgement"] == LLMJudgement.UNEXPLAINED


# ── Circuit breaker in classify ───────────────────────────────────────────────


class TestClassifyNodeCircuitBreaker:
    async def test_open_cb_returns_uncertain_without_llm_call(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()  # trips immediately (threshold=1)
        assert cb.is_open()

        chain = AsyncMock()
        node = make_classify_node(chain, circuit_breaker=cb)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        result = await node(state)
        assert result["llm_judgement"] == LLMJudgement.UNCERTAIN
        assert result.get("error") == "circuit_open"
        chain.ainvoke.assert_not_called()

    async def test_failure_increments_cb_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=5)
        chain = AsyncMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("fail"))
        node = make_classify_node(chain, circuit_breaker=cb)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        await node(state)
        assert cb.failure_count == 1

    async def test_success_resets_cb_count(self) -> None:
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        chain = _mock_chain(LLMJudgement.EXPLAINED)
        node = make_classify_node(chain, circuit_breaker=cb)
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        await node(state)
        assert cb.failure_count == 0

    async def test_no_cb_works_normally(self) -> None:
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        node = make_classify_node(chain)  # no circuit_breaker
        state: AnomalyAgentState = {**_base_state(), "news_articles": []}  # type: ignore[assignment]
        result = await node(state)
        assert result["llm_judgement"] == LLMJudgement.UNEXPLAINED


# ── Schedule recheck node ─────────────────────────────────────────────────────


class TestScheduleRecheckNode:
    def test_enqueues_task_for_unexplained(self) -> None:
        queue = RecheckQueue(max_size=10)
        node = make_schedule_recheck_node(queue, delay_min=20)
        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": LLMJudgement.UNEXPLAINED,
            "news_articles": [],
        }
        result = node(state)
        assert result == state  # state unchanged
        assert len(queue._scheduled) == 1
        assert "test-001" in queue._scheduled

    def test_enqueue_idempotent(self) -> None:
        queue = RecheckQueue(max_size=10)
        node = make_schedule_recheck_node(queue, delay_min=20)
        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": LLMJudgement.UNEXPLAINED,
            "news_articles": [],
        }
        node(state)
        node(state)  # second call — same alert_id
        assert len(queue._scheduled) == 1

    def test_recheck_at_is_in_future(self) -> None:
        queue = RecheckQueue(max_size=10)
        before = datetime.now(tz=timezone.utc)
        node = make_schedule_recheck_node(queue, delay_min=5)
        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": LLMJudgement.UNEXPLAINED,
            "news_articles": [],
        }
        node(state)
        task = queue._queue.get_nowait()
        assert task.recheck_at > before


# ── Route function ────────────────────────────────────────────────────────────


class TestRouteAfterClassify:
    def test_unexplained_routes_to_recheck(self) -> None:
        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": LLMJudgement.UNEXPLAINED,
        }
        assert _route_after_classify(state) == "schedule_recheck"

    def test_explained_routes_to_end(self) -> None:
        from langgraph.graph import END

        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": LLMJudgement.EXPLAINED,
        }
        assert _route_after_classify(state) == END

    def test_uncertain_routes_to_end(self) -> None:
        from langgraph.graph import END

        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": LLMJudgement.UNCERTAIN,
        }
        assert _route_after_classify(state) == END

    def test_none_judgement_routes_to_end(self) -> None:
        from langgraph.graph import END

        state: AnomalyAgentState = {  # type: ignore[assignment]
            **_base_state(),
            "llm_judgement": None,
        }
        assert _route_after_classify(state) == END


# ── Build graph integration ───────────────────────────────────────────────────


class TestBuildGraph:
    async def test_full_pipeline_explained(self) -> None:
        articles = [{"title": "AAPL Earnings Miss", "url": None, "source": "BB", "published_at": "2026-06-01T10:00:00Z"}]
        fetch_fn = MagicMock(return_value=articles)
        chain = _mock_chain(LLMJudgement.EXPLAINED, titles=["AAPL Earnings Miss"])
        graph = build_graph(fetch_fn, chain)
        result = await graph.ainvoke({"alert": _alert()})
        assert result["llm_judgement"] == LLMJudgement.EXPLAINED
        assert len(result["news_refs"]) == 1

    async def test_full_pipeline_uncertain_on_llm_error(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = AsyncMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        graph = build_graph(fetch_fn, chain)
        result = await graph.ainvoke({"alert": _alert()})
        assert result["llm_judgement"] == LLMJudgement.UNCERTAIN

    async def test_full_pipeline_no_news_unexplained(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        graph = build_graph(fetch_fn, chain)
        result = await graph.ainvoke({"alert": _alert()})
        assert result["llm_judgement"] == LLMJudgement.UNEXPLAINED
        assert result["news_refs"] == []

    async def test_unexplained_schedules_recheck_when_queue_provided(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        queue = RecheckQueue(max_size=10)
        graph = build_graph(fetch_fn, chain, recheck_queue=queue)
        await graph.ainvoke({"alert": _alert()})
        assert "test-001" in queue._scheduled

    async def test_explained_does_not_schedule_recheck(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.EXPLAINED)
        queue = RecheckQueue(max_size=10)
        graph = build_graph(fetch_fn, chain, recheck_queue=queue)
        await graph.ainvoke({"alert": _alert()})
        assert len(queue._scheduled) == 0

    async def test_circuit_breaker_wired_into_graph(self) -> None:
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()  # trips immediately
        fetch_fn = MagicMock(return_value=[])
        chain = AsyncMock()  # should never be called
        graph = build_graph(fetch_fn, chain, circuit_breaker=cb)
        result = await graph.ainvoke({"alert": _alert()})
        assert result["llm_judgement"] == LLMJudgement.UNCERTAIN
        chain.ainvoke.assert_not_called()
