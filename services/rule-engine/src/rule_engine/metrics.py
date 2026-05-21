from prometheus_client import Counter, Gauge

quotes_processed_total = Counter(
    "rule_engine_quotes_processed_total",
    "Total quote events processed",
)

quotes_skipped_total = Counter(
    "rule_engine_quotes_skipped_total",
    "Quotes skipped because symbol has no loaded context",
)

alerts_fired_total = Counter(
    "rule_engine_alerts_fired_total",
    "Alerts fired, labelled by rule and severity",
    ["rule_name", "severity"],
)

context_reload_total = Counter(
    "rule_engine_context_reload_total",
    "Number of context cache reloads triggered via /internal/reload-user-rules",
)

context_symbols_loaded = Gauge(
    "rule_engine_context_symbols_loaded",
    "Number of symbols currently loaded in the context cache",
)

custom_alerts_fired_total = Counter(
    "rule_engine_custom_alerts_fired_total",
    "Custom user alert rules fired",
    ["field", "operator"],
)

custom_rules_evaluated_total = Counter(
    "rule_engine_custom_rules_evaluated_total",
    "Total custom rules evaluated per quote",
)

telegram_alert_send_failures_total = Counter(
    "rule_engine_telegram_alert_send_failures_total",
    "Failed Telegram alert deliveries for custom user rules",
)

db_insert_failures_total = Counter(
    "rule_engine_db_insert_failures_total",
    "Failed database insert or update operations",
    ["operation"],
)
