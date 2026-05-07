"""
Prometheus metrics for finnhub-trades-producer.
Exported on :${METRICS_PORT}/metrics (default 8000).
"""

from prometheus_client import Counter, Histogram, start_http_server

TICKS_PUBLISHED = Counter(
    "trades_ticks_published_total",
    "Number of trade ticks successfully published to Kafka",
    ["symbol"],
)

TICKS_DROPPED = Counter(
    "trades_ticks_dropped_total",
    "Number of trade ticks dropped due to validation or serialisation errors",
    ["reason"],
)

WS_RECONNECTS = Counter(
    "trades_ws_reconnects_total",
    "Number of Finnhub WebSocket reconnections",
)

KAFKA_PUBLISH_LATENCY = Histogram(
    "trades_kafka_publish_latency_seconds",
    "Time taken to send one tick to Kafka",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)
