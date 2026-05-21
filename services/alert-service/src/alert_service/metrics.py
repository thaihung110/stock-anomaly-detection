from prometheus_client import Counter, Histogram

alerts_sent_total = Counter(
    "alert_service_alerts_sent_total",
    "Total Telegram messages sent successfully",
    ["rule_name", "severity"],
)

alerts_failed_total = Counter(
    "alert_service_alerts_failed_total",
    "Total Telegram send failures after all retries",
    ["rule_name", "severity"],
)

telegram_latency_seconds = Histogram(
    "alert_service_telegram_latency_seconds",
    "End-to-end latency for Telegram send (including retries)",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

alerts_consumed_total = Counter(
    "alert_service_alerts_consumed_total",
    "Total alerts consumed from Kafka alerts.raw",
)
