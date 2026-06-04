"""
Async Kafka producer wrapping aiokafka.

Publishes TradeTick messages to raw.stock.trades.
Key = symbol bytes → guarantees same partition for OHLCV window ordering.
"""

import structlog
from aiokafka import AIOKafkaProducer

from finnhub_trades_producer.config import Settings
from finnhub_trades_producer.schema import TradeTick

logger = structlog.get_logger(__name__)


class TradesProducer:
    def __init__(self, config: Settings) -> None:
        self._config = config
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._config.kafka_bootstrap_servers,
            compression_type=self._config.kafka_compression_type,
            acks="all",
            enable_idempotence=True,
        )
        await self._producer.start()
        logger.info(
            "kafka_producer_started",
            topic=self._config.kafka_topic,
            brokers=self._config.kafka_bootstrap_servers,
        )

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            logger.info("kafka_producer_stopped")

    async def publish(self, tick: TradeTick) -> None:
        assert self._producer is not None, "call start() before publish()"

        await self._producer.send(
            self._config.kafka_topic,
            key=tick.kafka_key(),
            value=tick.to_kafka_bytes(),
        )
