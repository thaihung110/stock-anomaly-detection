import json
from pydantic import BaseModel, field_validator


class QuoteEvent(BaseModel):
    """Kafka message schema for raw.stock.quotes.

    Field names and types must exactly match the Rule Engine QuoteEvent consumer.
    """
    symbol: str
    price: float
    change_pct: float
    day_volume: int
    day_high: float
    day_low: float
    prev_close: float
    event_ts: str  # ISO-8601 UTC string, e.g. "2026-03-26T10:23:15Z"

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, v: str) -> str:
        return v.upper()

    @field_validator("price")
    @classmethod
    def price_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"price must be > 0, got {v}")
        return v

    @field_validator("day_volume", mode="before")
    @classmethod
    def coerce_volume_to_int(cls, v: object) -> int:
        return int(v)

    def to_kafka_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")
