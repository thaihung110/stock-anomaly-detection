from prometheus_client import Counter, Histogram

ALERTS_RECEIVED = Counter(
    "llm_agent_alerts_received_total",
    "Total alerts received from alerts.raw",
)
ALERTS_CLASSIFIED = Counter(
    "llm_agent_alerts_classified_total",
    "Alerts after LLM classification, labelled by judgement",
    ["judgement"],
)
CLASSIFY_LATENCY = Histogram(
    "llm_agent_classify_seconds",
    "End-to-end latency of LLM classification per alert (news fetch + LLM call)",
    buckets=[0.5, 1, 2, 4, 8, 16, 32],
)
NEWS_FETCHED = Histogram(
    "llm_agent_news_fetched_count",
    "Number of news articles fetched per alert after union+dedup",
    buckets=[1, 2, 4, 8, 16],
)
FAIL_OPEN_TOTAL = Counter(
    "llm_agent_fail_open_total",
    "Alerts forwarded as UNCERTAIN due to agent timeout or LLM error",
)
