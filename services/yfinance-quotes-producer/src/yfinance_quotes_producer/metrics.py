from prometheus_client import Counter, Histogram, start_http_server

QUOTES_PUBLISHED = Counter(
    "quotes_published_total",
    "Total quote events successfully published to Kafka",
    ["symbol"],
)

QUOTES_DROPPED = Counter(
    "quotes_dropped_total",
    "Total quote events dropped before publishing",
    ["reason"],
)

YF_WS_RECONNECTS = Counter(
    "yf_ws_reconnects_total",
    "Total yfinance WebSocket reconnection attempts",
)

KAFKA_PUBLISH_LATENCY = Histogram(
    "kafka_publish_latency_seconds",
    "Latency for Kafka produce calls",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)
