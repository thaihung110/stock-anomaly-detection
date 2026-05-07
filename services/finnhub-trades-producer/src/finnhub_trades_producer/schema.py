"""
Kafka message contract for topic raw.stock.trades.

Source of truth: spark-application/trades-ohlcv-stream/src/main/scala/
    com/stockanomalydetection/tradesohlcv/schema/TradeSchema.scala

Spark schema:
    symbol       StringType   not null
    price        DoubleType   not null
    volume       LongType     not null   <- must be int, not float
    timestamp_ms LongType     not null   <- epoch milliseconds
    conditions   ArrayType[String] nullable

Spark timestamp derivation:
    bar_ts = (timestamp_ms / 1000L).cast(TimestampType)
    → timestamp_ms MUST be epoch milliseconds (integer)
"""

import json
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


class TradeTick(BaseModel):
    symbol: str
    price: float
    volume: int
    timestamp_ms: int
    conditions: Optional[list[str]] = None

    @field_validator("volume", mode="before")
    @classmethod
    def coerce_volume_to_int(cls, v: object) -> int:
        # Finnhub may send volume as a float (e.g. 150.0)
        return int(v)

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"price must be > 0, got {v}")
        return v

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("symbol must not be empty")
        return v.strip().upper()

    @model_validator(mode="after")
    def timestamp_must_look_like_ms(self) -> "TradeTick":
        # Epoch ms for 2020-01-01 is ~1577836800000; sanity-check lower bound
        if self.timestamp_ms < 1_000_000_000_000:
            raise ValueError(
                f"timestamp_ms={self.timestamp_ms} looks like seconds, not milliseconds"
            )
        return self

    def to_kafka_bytes(self) -> bytes:
        """Serialise to UTF-8 JSON bytes for Kafka value."""
        data = {
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "timestamp_ms": self.timestamp_ms,
            "conditions": self.conditions,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def kafka_key(self) -> bytes:
        """Partition key — route all ticks for a symbol to the same partition."""
        return self.symbol.encode("utf-8")
