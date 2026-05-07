import time

import structlog
from aiokafka import AIOKafkaProducer

from .config import Settings
from .metrics import KAFKA_PUBLISH_LATENCY, QUOTES_PUBLISHED
from .schema import QuoteEvent

log = structlog.get_logger()


class QuotesProducer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.kafka_bootstrap_servers,
            compression_type=self._settings.kafka_compression_type,
            acks="all",
            enable_idempotence=True,
        )
        await self._producer.start()
        log.info("kafka_producer_started", topic=self._settings.kafka_topic)

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            log.info("kafka_producer_stopped")

    async def publish(self, event: QuoteEvent) -> None:
        assert self._producer is not None, "producer not started"
        start = time.monotonic()
        await self._producer.send(
            self._settings.kafka_topic,
            key=event.symbol.encode("utf-8"),
            value=event.to_kafka_bytes(),
        )
        KAFKA_PUBLISH_LATENCY.observe(time.monotonic() - start)
        QUOTES_PUBLISHED.labels(symbol=event.symbol).inc()
