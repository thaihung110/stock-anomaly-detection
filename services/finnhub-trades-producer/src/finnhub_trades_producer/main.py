"""
Entrypoint for finnhub-trades-producer.

Connects to Finnhub WebSocket, normalizes each trade tick,
and publishes it to Kafka topic raw.stock.trades.
"""

import asyncio
import signal
import sys

import structlog
from pydantic import ValidationError

from finnhub_trades_producer.config import Settings
from finnhub_trades_producer.finnhub_client import stream_trades
from finnhub_trades_producer.metrics import TICKS_DROPPED, start_metrics_server
from finnhub_trades_producer.normalizer import normalize
from finnhub_trades_producer.producer import TradesProducer

logger = structlog.get_logger(__name__)

_LOG_EVERY_N_TICKS = 100


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


async def _run(config: Settings, producer: TradesProducer) -> None:
    ticks_published = 0
    ticks_dropped = 0

    async for raw_ticks in stream_trades(config):
        for raw_tick in raw_ticks:
            try:
                tick = normalize(raw_tick)
                await producer.publish(tick)
                ticks_published += 1
                if ticks_published % _LOG_EVERY_N_TICKS == 0:
                    logger.info(
                        "publish_progress",
                        ticks_published=ticks_published,
                        ticks_dropped=ticks_dropped,
                        last_symbol=tick.symbol,
                    )
            except ValidationError as exc:
                ticks_dropped += 1
                TICKS_DROPPED.labels(reason="validation_error").inc()
                logger.warning("tick_dropped_validation", error=str(exc), raw=raw_tick)
            except KeyError as exc:
                ticks_dropped += 1
                TICKS_DROPPED.labels(reason="missing_field").inc()
                logger.warning("tick_dropped_missing_field", field=str(exc), raw=raw_tick)
            except Exception as exc:  # noqa: BLE001
                ticks_dropped += 1
                TICKS_DROPPED.labels(reason="unexpected").inc()
                logger.error("tick_dropped_unexpected", error=str(exc), raw=raw_tick)


async def main() -> None:
    _configure_logging()
    config = Settings()

    start_metrics_server(config.metrics_port)
    logger.info(
        "service_starting",
        topic=config.kafka_topic,
        symbols_count=len(config.symbols_list),
        metrics_port=config.metrics_port,
    )

    producer = TradesProducer(config)
    await producer.start()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        run_task = asyncio.create_task(_run(config, producer))
        stop_task = asyncio.create_task(stop_event.wait())

        done, pending = await asyncio.wait(
            {run_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        # Re-raise any unexpected exception from _run
        for task in done:
            if task is run_task and not task.cancelled():
                task.result()

    finally:
        await producer.stop()
        logger.info("service_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
