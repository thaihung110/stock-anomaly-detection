import asyncio
import signal

import structlog

from .config import Settings
from .metrics import QUOTES_DROPPED, start_metrics_server
from .normalizer import normalize
from .producer import QuotesProducer
from .yf_client import stream_quotes

log = structlog.get_logger()

_LOG_EVERY_N_QUOTES = 100


async def run(settings: Settings) -> None:
    producer = QuotesProducer(settings)
    await producer.start()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _shutdown() -> None:
        log.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    quotes_published = 0
    quotes_dropped = 0

    try:
        async for raw in stream_quotes(settings):
            if stop_event.is_set():
                break
            try:
                event = normalize(raw)
                await producer.publish(event)
                quotes_published += 1
                if quotes_published % _LOG_EVERY_N_QUOTES == 0:
                    log.info(
                        "publish_progress",
                        quotes_published=quotes_published,
                        quotes_dropped=quotes_dropped,
                        last_symbol=event.symbol,
                    )
            except KeyError as exc:
                quotes_dropped += 1
                QUOTES_DROPPED.labels(reason="missing_field").inc()
                log.warning(
                    "quote_dropped_missing_field",
                    missing_field=str(exc),
                    symbol=raw.get("id", "<unknown>"),
                    present_fields=sorted(raw.keys()),
                )
            except Exception as exc:
                quotes_dropped += 1
                QUOTES_DROPPED.labels(reason="validation_error").inc()
                log.warning(
                    "quote_dropped_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    symbol=raw.get("id", "<unknown>"),
                    present_fields=sorted(raw.keys()),
                )
    finally:
        await producer.stop()


def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    settings = Settings()
    start_metrics_server(settings.metrics_port)
    log.info(
        "yfinance_quotes_producer_starting",
        kafka_topic=settings.kafka_topic,
        symbols_count=len(settings.symbols_list),
        metrics_port=settings.metrics_port,
    )

    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
