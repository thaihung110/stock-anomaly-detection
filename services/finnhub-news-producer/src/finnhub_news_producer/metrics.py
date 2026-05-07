"""
Prometheus metrics for finnhub-news-producer.
Exported on :${METRICS_PORT}/metrics (default 8000).
"""

from prometheus_client import Counter, Histogram, start_http_server

ARTICLES_PUBLISHED = Counter(
    "news_articles_published_total",
    "Number of news articles successfully published to Kafka",
    ["symbol"],
)

ARTICLES_DROPPED = Counter(
    "news_articles_dropped_total",
    "Number of articles dropped due to dedup, validation, or missing fields",
    ["reason"],
)

API_ERRORS = Counter(
    "news_api_errors_total",
    "Number of Finnhub API errors encountered during polling",
    ["symbol"],
)

POLL_DURATION = Histogram(
    "news_poll_duration_seconds",
    "Time taken to complete one full poll cycle across all symbols",
    buckets=(5, 10, 30, 60, 90, 120, 180, 300),
)

KAFKA_PUBLISH_LATENCY = Histogram(
    "news_kafka_publish_latency_seconds",
    "Time taken to send one article to Kafka",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)
