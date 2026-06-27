"""LLM Agent service entrypoint.

Consumes alerts.raw (AlertEvent), runs the LangGraph classification pipeline,
and publishes ConfirmedAlertEvent to alerts.confirmed.

Safety guarantees (Stage C):
  - Fail-open: timeout / unhandled error → UNCERTAIN (alert never blocked).
  - Dedup cache: duplicate alert_id within DEDUP_CACHE_TTL_SEC is silently dropped.
  - Circuit breaker: consecutive LLM failures → fast-fail to UNCERTAIN until recovery.
  - Recheck queue: UNEXPLAINED alerts get one follow-up at RECHECK_DELAY_MIN.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import FastAPI
from faststream.kafka.fastapi import KafkaRouter
from prometheus_client import make_asgi_app

from llm_agent.config import Settings
from llm_agent.graph.build import build_graph
from llm_agent.infrastructure.circuit_breaker import CircuitBreaker
from llm_agent.infrastructure.dedup_cache import DedupCache
from llm_agent.infrastructure.news_reader import fetch_news
from llm_agent.infrastructure.publisher import AlertPublisher
from llm_agent.infrastructure.recheck_queue import RecheckQueue
from llm_agent.llm.prompts import CLASSIFY_PROMPT
from llm_agent.metrics import ALERTS_CLASSIFIED, ALERTS_RECEIVED, FAIL_OPEN_TOTAL
from llm_agent.schema import AlertEvent, ConfirmedAlertEvent, LLMJudgement

logger = structlog.get_logger(__name__)

cfg = Settings()
router = KafkaRouter(cfg.kafka_bootstrap_servers)
_confirmed_pub = router.publisher(cfg.kafka_output_topic)
_followup_pub = router.publisher(cfg.kafka_followup_topic)

_graph: Any = None
_publisher: AlertPublisher | None = None
_dedup_cache: DedupCache | None = None
_recheck_task: asyncio.Task[None] | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _graph, _publisher, _dedup_cache, _recheck_task

    from llm_agent.llm.factory import build_llm  # lazy — provider package optional at import

    llm_client = build_llm(cfg)
    # Build chain once; share between the graph's classify node and the recheck worker.
    classify_chain = CLASSIFY_PROMPT | llm_client

    _dedup_cache = DedupCache(ttl_sec=cfg.dedup_cache_ttl_sec)
    circuit_breaker = CircuitBreaker(
        failure_threshold=cfg.cb_failure_threshold,
        recovery_timeout_sec=cfg.cb_recovery_timeout_sec,
    )

    recheck_queue: RecheckQueue | None = None
    if cfg.recheck_enabled:
        recheck_queue = RecheckQueue(max_size=cfg.recheck_queue_max_size)

    _graph = build_graph(
        lambda sym: fetch_news(sym, cfg),
        classify_chain,
        recheck_queue=recheck_queue,
        circuit_breaker=circuit_breaker,
        recheck_delay_min=cfg.recheck_delay_min,
    )
    _publisher = AlertPublisher(_confirmed_pub, _followup_pub)

    if recheck_queue is not None and _publisher is not None:
        _recheck_task = asyncio.create_task(
            recheck_queue.run(
                lambda sym: fetch_news(sym, cfg),
                classify_chain,
                _publisher,
            )
        )

    logger.info(
        "llm_agent_started",
        llm_model=cfg.llm_model,
        kafka_input=cfg.kafka_input_topic,
        kafka_output=cfg.kafka_output_topic,
        agent_ttl_sec=cfg.agent_ttl_sec,
        recheck_enabled=cfg.recheck_enabled,
    )

    async with router.lifespan_context(_):
        yield

    if _recheck_task is not None and not _recheck_task.done():
        _recheck_task.cancel()
        try:
            await _recheck_task
        except asyncio.CancelledError:
            pass

    logger.info("llm_agent_stopped")


app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.mount("/metrics", make_asgi_app())


@router.subscriber(cfg.kafka_input_topic, group_id=cfg.kafka_consumer_group)
async def handle_alert(event: AlertEvent) -> None:
    """Classify an AlertEvent from alerts.raw and publish ConfirmedAlertEvent."""
    ALERTS_RECEIVED.inc()

    # Idempotency: skip if this alert_id was already processed
    if _dedup_cache is not None and _dedup_cache.is_seen(event.alert_id):
        logger.info("alert_deduplicated", alert_id=event.alert_id, symbol=event.symbol)
        return

    logger.info(
        "alert_received",
        alert_id=event.alert_id,
        symbol=event.symbol,
        rule=event.rule_name.value,
    )

    if _graph is None or _publisher is None:
        logger.error("agent_not_initialized", alert_id=event.alert_id)
        return

    _fail_open: dict[str, Any] = {
        "alert": event,
        "llm_judgement": LLMJudgement.UNCERTAIN,
        "news_refs": [],
        "news_category": None,
        "explanation": None,
        "news_summary": None,
        "error": None,
    }

    try:
        async with asyncio.timeout(cfg.agent_ttl_sec):
            result: dict[str, Any] = await _graph.ainvoke({"alert": event})
    except TimeoutError:
        FAIL_OPEN_TOTAL.inc()
        logger.warning(
            "agent_timeout_fail_open", alert_id=event.alert_id, ttl_sec=cfg.agent_ttl_sec
        )
        result = _fail_open
    except Exception as exc:
        FAIL_OPEN_TOTAL.inc()
        logger.error("agent_error_fail_open", alert_id=event.alert_id, error=str(exc))
        result = _fail_open

    judgement: LLMJudgement = result.get("llm_judgement") or LLMJudgement.UNCERTAIN
    ALERTS_CLASSIFIED.labels(judgement=judgement.value).inc()

    confirmed = ConfirmedAlertEvent(
        **event.model_dump(),
        llm_judgement=judgement,
        final_explanation=result.get("explanation"),
        news_summary=result.get("news_summary"),
        news_category=result.get("news_category"),
        news_refs=result.get("news_refs") or [],
    )
    await _publisher.publish_confirmed(confirmed)

    if _dedup_cache is not None:
        _dedup_cache.mark_seen(event.alert_id)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
