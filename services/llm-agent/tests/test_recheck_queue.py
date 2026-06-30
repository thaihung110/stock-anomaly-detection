"""Tests for RecheckQueue — bounded follow-up re-check queue.

Verifies the 3 policy branches (§5.3 plan):
  FLIP    : UNEXPLAINED → EXPLAINED   → FollowUpEvent emitted
  CONFIRM : UNEXPLAINED → UNEXPLAINED → FollowUpEvent emitted
  SILENT  : re-classify as UNCERTAIN   → nothing emitted
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_agent.infrastructure.publisher import AlertPublisher
from llm_agent.infrastructure.recheck_queue import RecheckQueue, RecheckTask
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
        "alert_id": "rc-test-001",
        "symbol": "NVDA",
        "event_ts": "2026-06-01T14:00:00Z",
        "rule_name": RuleName.VOLUME_ZSCORE,
        "severity": AlertSeverity.HIGH,
        "triggered_value": 5.2,
        "threshold": 3.0,
        "context_snapshot": {"z_vol": 5.2},
    }
    return AlertEvent(**{**defaults, **overrides})  # type: ignore[arg-type]


def _task(alert: AlertEvent | None = None, **overrides: object) -> RecheckTask:
    a = alert or _alert()
    defaults: dict = {
        "alert_id": a.alert_id,
        "symbol": a.symbol,
        "original_judgement": LLMJudgement.UNEXPLAINED,
        "recheck_at": datetime.now(tz=timezone.utc),
        "alert": a,
    }
    defaults.update(overrides)
    return RecheckTask(**defaults)


def _mock_chain(
    judgement: LLMJudgement,
    titles: list[str] | None = None,
) -> AsyncMock:
    result = ClassifyResult(
        judgement=judgement,
        category=NewsCategory.EARNINGS if judgement == LLMJudgement.EXPLAINED else None,
        news_summary="Late news found" if judgement == LLMJudgement.EXPLAINED else None,
        relevant_titles=titles or [],
    )
    chain = AsyncMock()
    chain.ainvoke = AsyncMock(return_value=result)
    return chain


def _mock_publisher() -> tuple[AlertPublisher, AsyncMock, AsyncMock]:
    confirmed_pub = AsyncMock()
    followup_pub = AsyncMock()
    return AlertPublisher(confirmed_pub, followup_pub), confirmed_pub, followup_pub


# ── Queue mechanics ───────────────────────────────────────────────────────────


class TestRecheckQueueMechanics:
    def test_enqueue_returns_true_on_success(self) -> None:
        q = RecheckQueue(max_size=10)
        assert q.enqueue_nowait(_task()) is True

    def test_enqueue_idempotent(self) -> None:
        q = RecheckQueue(max_size=10)
        assert q.enqueue_nowait(_task()) is True
        assert q.enqueue_nowait(_task()) is False
        assert len(q._scheduled) == 1

    def test_enqueue_respects_max_size(self) -> None:
        q = RecheckQueue(max_size=1)
        q.enqueue_nowait(_task(_alert(alert_id="id-1")))
        result = q.enqueue_nowait(_task(_alert(alert_id="id-2")))
        assert result is False

    def test_different_alert_ids_both_enqueued(self) -> None:
        q = RecheckQueue(max_size=10)
        q.enqueue_nowait(_task(_alert(alert_id="id-1")))
        q.enqueue_nowait(_task(_alert(alert_id="id-2")))
        assert len(q._scheduled) == 2


# ── Re-check branches ─────────────────────────────────────────────────────────


class TestRecheckBranches:
    async def test_flip_unexplained_to_explained_emits_followup(self) -> None:
        articles = [{"title": "Morgan Stanley upgrade", "url": None, "source": "CNBC", "published_at": "2026-06-01T14:20:00Z"}]
        fetch_fn = MagicMock(return_value=articles)
        chain = _mock_chain(LLMJudgement.EXPLAINED, titles=["Morgan Stanley upgrade"])
        publisher, _, followup_pub = _mock_publisher()

        q = RecheckQueue(max_size=10)
        await q._process(_task(), fetch_fn, chain, publisher)

        followup_pub.publish.assert_awaited_once()
        data = json.loads(followup_pub.publish.call_args[0][0].decode())
        assert data["prev_judgement"] == "UNEXPLAINED"
        assert data["new_judgement"] == "EXPLAINED"
        assert data["ref_alert_id"] == "rc-test-001"

    async def test_confirm_unexplained_stays_unexplained_emits(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        publisher, _, followup_pub = _mock_publisher()

        q = RecheckQueue(max_size=10)
        await q._process(_task(), fetch_fn, chain, publisher)

        followup_pub.publish.assert_awaited_once()
        data = json.loads(followup_pub.publish.call_args[0][0].decode())
        assert data["new_judgement"] == "UNEXPLAINED"

    async def test_uncertain_result_does_not_emit(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNCERTAIN)
        publisher, _, followup_pub = _mock_publisher()

        q = RecheckQueue(max_size=10)
        await q._process(_task(), fetch_fn, chain, publisher)
        followup_pub.publish.assert_not_awaited()

    async def test_llm_error_does_not_emit(self) -> None:
        fetch_fn = MagicMock(return_value=[])
        chain = AsyncMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM gone"))
        publisher, _, followup_pub = _mock_publisher()

        q = RecheckQueue(max_size=10)
        await q._process(_task(), fetch_fn, chain, publisher)
        followup_pub.publish.assert_not_awaited()

    async def test_news_fetch_error_classifies_on_empty(self) -> None:
        fetch_fn = MagicMock(side_effect=ConnectionError("catalog down"))
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        publisher, _, followup_pub = _mock_publisher()

        q = RecheckQueue(max_size=10)
        await q._process(_task(), fetch_fn, chain, publisher)
        followup_pub.publish.assert_awaited_once()

    async def test_relevance_gate_excludes_hallucinated_title(self) -> None:
        articles = [{"title": "Real Story", "url": None, "source": "AP", "published_at": "2026-06-01T14:20:00Z"}]
        fetch_fn = MagicMock(return_value=articles)
        chain = _mock_chain(LLMJudgement.EXPLAINED, titles=["HALLUCINATED TITLE"])
        publisher, _, followup_pub = _mock_publisher()

        q = RecheckQueue(max_size=10)
        await q._process(_task(), fetch_fn, chain, publisher)
        data = json.loads(followup_pub.publish.call_args[0][0].decode())
        assert data["news_refs"] == []

    async def test_recheck_waits_for_scheduled_time(self) -> None:
        future_time = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        publisher, _, _ = _mock_publisher()

        q = RecheckQueue(max_size=10)
        task = _task(recheck_at=future_time)

        with patch("llm_agent.infrastructure.recheck_queue.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            await q._process(task, fetch_fn, chain, publisher)
            mock_sleep.assert_awaited_once()
            wait_sec = mock_sleep.call_args[0][0]
            assert wait_sec > 0

    async def test_past_recheck_at_does_not_sleep(self) -> None:
        past_time = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        publisher, _, _ = _mock_publisher()

        q = RecheckQueue(max_size=10)
        task = _task(recheck_at=past_time)

        with patch("llm_agent.infrastructure.recheck_queue.asyncio.sleep") as mock_sleep:
            await q._process(task, fetch_fn, chain, publisher)
            mock_sleep.assert_not_awaited()

    async def test_scheduled_discarded_after_processing(self) -> None:
        q = RecheckQueue(max_size=10)
        task = _task()
        q.enqueue_nowait(task)
        assert task.alert_id in q._scheduled

        fetch_fn = MagicMock(return_value=[])
        chain = _mock_chain(LLMJudgement.UNEXPLAINED)
        publisher, _, _ = _mock_publisher()

        dequeued = await q._queue.get()
        try:
            await q._process(dequeued, fetch_fn, chain, publisher)
        finally:
            q._scheduled.discard(dequeued.alert_id)
            q._queue.task_done()

        assert task.alert_id not in q._scheduled
