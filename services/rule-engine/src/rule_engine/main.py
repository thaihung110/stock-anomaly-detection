import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog
from fastapi import FastAPI
from faststream.kafka.fastapi import KafkaRouter

from rule_engine.config import Settings
from rule_engine.infrastructure.context_loader import load_context
from rule_engine.infrastructure.db.client import DbClient
from rule_engine.infrastructure.db.repository import UserAlertRepository
from rule_engine.application.rule_orchestrator import RuleOrchestrator
from rule_engine.domain.schema import QuoteEvent, ReloadResponse
from rule_engine.application.user_alert_processor import UserAlertProcessor

logger = structlog.get_logger(__name__)

cfg = Settings()
router = KafkaRouter(cfg.kafka_bootstrap_servers)
publisher = router.publisher(cfg.kafka_output_topic)
user_alert_publisher = router.publisher(cfg.kafka_user_alert_topic)

_context_cache: dict[str, dict[str, float]] = {}
_reload_lock = asyncio.Lock()

_db_client: DbClient | None = None
_orchestrator: RuleOrchestrator | None = None
_alert_processor: UserAlertProcessor | None = None


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _context_cache, _db_client, _orchestrator, _alert_processor

    _context_cache = await asyncio.to_thread(load_context, cfg)

    _db_client = DbClient(cfg.pg_dsn)
    await _db_client.connect()

    repository = UserAlertRepository(_db_client)
    _orchestrator = RuleOrchestrator(cfg)
    _alert_processor = UserAlertProcessor(repository)
    rule_count = await _alert_processor.reload_rules()

    logger.info(
        "rule_engine_started",
        symbol_count=len(_context_cache),
        user_rules_count=rule_count,
    )
    async with router.lifespan_context(_):
        yield

    await _db_client.close()


app = FastAPI(lifespan=lifespan)
app.include_router(router)


@router.subscriber(cfg.kafka_input_topic)
async def handle_quote(event: QuoteEvent) -> None:
    ctx = _context_cache.get(event.symbol)
    if ctx is None:
        logger.debug("symbol_not_in_context", symbol=event.symbol)
        return

    if _orchestrator is not None:
        await _orchestrator.evaluate(event, ctx, publisher)

    if _alert_processor is not None:
        await _alert_processor.evaluate(event, ctx, user_alert_publisher)
        await _alert_processor.update_prev_values(event, ctx)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "symbols_loaded": str(len(_context_cache))}


@app.post("/internal/reload-user-rules", response_model=ReloadResponse)
async def reload_rules() -> ReloadResponse:
    global _context_cache

    async with _reload_lock:
        try:
            new_context = await asyncio.to_thread(load_context, cfg)
        except Exception as exc:
            logger.error("context_reload_load_failed", error=str(exc))
            return ReloadResponse(status="error", symbol_count=len(_context_cache))

        if not new_context:
            logger.warning(
                "context_reload_empty_skipped",
                current_symbol_count=len(_context_cache),
            )
            return ReloadResponse(status="error", symbol_count=len(_context_cache))

        _context_cache = new_context

    rule_count = 0
    if _alert_processor is not None:
        rule_count = await _alert_processor.reload_rules()

    logger.info(
        "rules_reloaded",
        symbol_count=len(_context_cache),
        user_rules_count=rule_count,
    )
    return ReloadResponse(status="ok", symbol_count=len(_context_cache))


