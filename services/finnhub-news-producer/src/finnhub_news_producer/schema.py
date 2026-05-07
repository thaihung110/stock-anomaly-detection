"""
Kafka message contract for topic raw.stock.news.

Source of truth for Spark consumer:
    spark-application/news-ingest-stream

Message schema (JSON):
    article_id      str   — MD5(url), dedup key
    symbol          str   — e.g. "AAPL"
    headline        str
    summary         str | null
    url             str
    source          str   — e.g. "Reuters"
    category        str | null
    published_at_ms int   — epoch milliseconds
    fetched_at_ms   int   — epoch milliseconds
"""

import hashlib
import json
import time

from pydantic import BaseModel, field_validator


class NewsArticle(BaseModel):
    article_id: str
    symbol: str
    headline: str
    summary: str | None = None
    url: str
    source: str
    category: str | None = None
    published_at_ms: int
    fetched_at_ms: int

    @field_validator("symbol")
    @classmethod
    def symbol_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("headline")
    @classmethod
    def headline_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("headline must not be empty")
        return v.strip()

    @field_validator("published_at_ms")
    @classmethod
    def published_at_looks_like_ms(cls, v: int) -> int:
        # Finnhub returns Unix seconds; normalizer multiplies by 1000
        if v < 1_000_000_000_000:
            raise ValueError(
                f"published_at_ms={v} looks like seconds, not milliseconds"
            )
        return v

    @staticmethod
    def make_article_id(url: str) -> str:
        return hashlib.md5(url.encode("utf-8")).hexdigest()  # noqa: S324

    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    def to_kafka_bytes(self) -> bytes:
        data = {
            "article_id": self.article_id,
            "symbol": self.symbol,
            "headline": self.headline,
            "summary": self.summary,
            "url": self.url,
            "source": self.source,
            "category": self.category,
            "published_at_ms": self.published_at_ms,
            "fetched_at_ms": self.fetched_at_ms,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def kafka_key(self) -> bytes:
        return self.symbol.encode("utf-8")
