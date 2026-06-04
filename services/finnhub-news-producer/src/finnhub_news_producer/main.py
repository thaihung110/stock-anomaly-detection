"""
Entrypoint for finnhub-news-producer.

Polls Finnhub /company-news every poll_interval_sec for all configured symbols,
deduplicates articles, and publishes them to Kafka topic raw.stock.news.
"""

import asyncio
import signal
import sys
import time

import structlog
from pydantic import ValidationError

from finnhub_news_producer.config import Settings
from finnhub_news_producer.finnhub_client import poll_news
from finnhub_news_producer.normalizer import normalize
from finnhub_news_producer.producer import NewsProducer

logger = structlog.get_logger(__name__)

_LOG_EVERY_N_ARTICLES = 50


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


async def _run(config: Settings, producer: NewsProducer, stop_event: asyncio.Event) -> None:
    seen_ids: set[str] = set()
    articles_published = 0
    articles_dropped = 0

    while not stop_event.is_set():
        cycle_start = time.monotonic()
        cycle_published = 0

        async for symbol, raw in poll_news(config, seen_ids):
            if stop_event.is_set():
                break
            try:
                article = normalize(raw, symbol)
                await producer.publish(article)
                articles_published += 1
                cycle_published += 1
                if articles_published % _LOG_EVERY_N_ARTICLES == 0:
                    logger.info(
                        "publish_progress",
                        articles_published=articles_published,
                        articles_dropped=articles_dropped,
                        last_symbol=article.symbol,
                    )
            except KeyError as exc:
                articles_dropped += 1
                logger.warning("article_dropped_missing_field", field=str(exc), symbol=symbol)
            except ValidationError as exc:
                articles_dropped += 1
                logger.warning("article_dropped_validation", error=str(exc), symbol=symbol)
            except Exception as exc:  # noqa: BLE001
                articles_dropped += 1
                logger.error("article_dropped_unexpected", error=str(exc), symbol=symbol)

        cycle_duration = time.monotonic() - cycle_start
        logger.info(
            "poll_cycle_complete",
            cycle_published=cycle_published,
            cycle_duration_sec=round(cycle_duration, 1),
            total_published=articles_published,
            total_dropped=articles_dropped,
        )

        # Sleep remaining time until next poll interval
        sleep_sec = max(0.0, config.poll_interval_sec - cycle_duration)
        if sleep_sec > 0 and not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_sec)
            except asyncio.TimeoutError:
                pass


async def main() -> None:
    _configure_logging()
    config = Settings()

    logger.info(
        "service_starting",
        topic=config.kafka_topic,
        symbols_count=len(config.symbols_list),
        poll_interval_sec=config.poll_interval_sec,
    )

    producer = NewsProducer(config)
    await producer.start()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal)

    try:
        await _run(config, producer, stop_event)
    finally:
        await producer.stop()
        logger.info("service_stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
