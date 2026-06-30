"""Bounded async queue for UNEXPLAINED alert follow-up re-checks (Bước 8).

Policy (§5.3 plan):
  - Exactly one re-check per alert_id at RECHECK_DELAY_MIN after the original alert.
  - Emit FollowUpEvent on FLIP   (UNEXPLAINED → EXPLAINED): news arrived after the alert.
  - Emit FollowUpEvent on CONFIRM (UNEXPLAINED → UNEXPLAINED): window expired, still no news.
  - Stay silent on UNCERTAIN re-check result: not enough signal to change the verdict.
  - Stay silent when queue is full (bounded): shed load gracefully under surge.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from llm_agent.infrastructure.publisher import AlertPublisher
from llm_agent.llm.prompts import build_prompt_vars
from llm_agent.schema import AlertEvent, FollowUpEvent, LLMJudgement, NewsRef

logger = structlog.get_logger(__name__)


@dataclass
class RecheckTask:
    """All context needed to perform one follow-up re-check."""

    alert_id: str
    symbol: str
    original_judgement: LLMJudgement  # always UNEXPLAINED at enqueue time
    recheck_at: datetime  # wall-clock time to run the re-check
    alert: AlertEvent  # original alert — used to rebuild prompt_vars


class RecheckQueue:
    """Bounded async queue processing UNEXPLAINED alerts for re-classification.

    Args:
        max_size: Maximum number of pending re-checks (drops on full).
    """

    def __init__(self, max_size: int = 1_000) -> None:
        self._queue: asyncio.Queue[RecheckTask] = asyncio.Queue(maxsize=max_size)
        self._scheduled: set[str] = set()  # alert_ids in queue

    def enqueue_nowait(self, task: RecheckTask) -> bool:
        """Add a re-check task without blocking.

        Returns True if enqueued, False if dropped (queue full or duplicate).
        Idempotent: same alert_id is never enqueued twice.
        """
        if task.alert_id in self._scheduled:
            logger.debug("recheck_already_scheduled", alert_id=task.alert_id)
            return False
        try:
            self._queue.put_nowait(task)
            self._scheduled.add(task.alert_id)
            logger.info(
                "recheck_scheduled",
                alert_id=task.alert_id,
                symbol=task.symbol,
                recheck_at=task.recheck_at.isoformat(),
            )
            return True
        except asyncio.QueueFull:
            logger.warning("recheck_queue_full_drop", alert_id=task.alert_id)
            return False

    async def run(
        self,
        fetch_news: Callable[[str], list[dict[str, str | None]]],
        classify_chain: Any,
        publisher: AlertPublisher,
    ) -> None:
        """Background worker — drains the queue and processes each task.

        Designed to run as an asyncio.Task for the service lifetime:
            asyncio.create_task(recheck_queue.run(...))
        """
        while True:
            task = await self._queue.get()
            try:
                await self._process(task, fetch_news, classify_chain, publisher)
            except Exception as exc:
                logger.error(
                    "recheck_process_error",
                    alert_id=task.alert_id,
                    error=str(exc),
                )
            finally:
                self._scheduled.discard(task.alert_id)
                self._queue.task_done()

    async def _process(
        self,
        task: RecheckTask,
        fetch_news: Callable[[str], list[dict[str, str | None]]],
        classify_chain: Any,
        publisher: AlertPublisher,
    ) -> None:
        """Wait until recheck_at, re-fetch news, re-classify, emit if warranted."""
        now = datetime.now(tz=timezone.utc)
        wait_sec = (task.recheck_at - now).total_seconds()
        if wait_sec > 0:
            await asyncio.sleep(wait_sec)

        # Re-fetch news — may now include articles that weren't available at alert time
        try:
            articles = await asyncio.to_thread(fetch_news, task.symbol)
        except Exception as exc:
            logger.warning(
                "recheck_news_fetch_failed", alert_id=task.alert_id, error=str(exc)
            )
            articles = []

        # Re-classify with the same prompt format
        prompt_vars = build_prompt_vars(task.alert, articles)
        try:
            result = await classify_chain.ainvoke(prompt_vars)
            new_judgement: LLMJudgement = result.judgement
        except Exception as exc:
            logger.error(
                "recheck_classify_failed", alert_id=task.alert_id, error=str(exc)
            )
            return  # silent — no FollowUpEvent on LLM error during re-check

        # Policy: emit only on FLIP or CONFIRM; stay silent on UNCERTAIN
        if new_judgement == LLMJudgement.UNCERTAIN:
            logger.info(
                "recheck_uncertain_skip",
                alert_id=task.alert_id,
                symbol=task.symbol,
            )
            return

        # Build news_refs for the FollowUpEvent (relevance gate applied).
        # Walrus operator narrows type: t is str (never None) inside this comprehension.
        title_index: dict[str, dict[str, str | None]] = {
            t: a for a in articles if (t := a.get("title"))
        }
        news_refs: list[NewsRef] = [
            NewsRef(
                title=t,
                url=title_index[t].get("url"),
                published_at=title_index[t].get("published_at") or "",
                source=title_index[t].get("source"),
            )
            for t in result.relevant_titles
            if t in title_index
        ]

        followup = FollowUpEvent(
            ref_alert_id=task.alert_id,
            symbol=task.symbol,
            prev_judgement=task.original_judgement,
            new_judgement=new_judgement,
            news_summary=result.news_summary,
            news_refs=news_refs,
            emitted_at=datetime.now(tz=timezone.utc).isoformat(),
            # Analytics fields (Stage D): carry original alert context for
            # time-to-explanation and anomaly_judgement write without a join.
            event_ts=task.alert.event_ts,
            rule_name=task.alert.rule_name,
        )
        await publisher.publish_followup(followup)

        is_flip = task.original_judgement != new_judgement
        logger.info(
            "recheck_emitted",
            alert_id=task.alert_id,
            symbol=task.symbol,
            event_type="FLIP" if is_flip else "CONFIRM",
            new_judgement=new_judgement.value,
        )
