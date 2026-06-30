"""Tests for Settings defaults and field types."""
from llm_agent.config import Settings


class TestSettings:
    def test_kafka_defaults(self) -> None:
        cfg = Settings()
        assert cfg.kafka_input_topic == "alerts.raw"
        assert cfg.kafka_output_topic == "alerts.confirmed"
        assert cfg.kafka_followup_topic == "alerts.followup"
        assert cfg.kafka_consumer_group == "llm-agent"

    def test_llm_defaults(self) -> None:
        cfg = Settings()
        assert cfg.llm_model == "google_genai:gemini-2.5-flash-lite"
        assert cfg.agent_ttl_sec == 8.0
        assert cfg.llm_escalation_model == ""

    def test_bronze_catalog_defaults(self) -> None:
        cfg = Settings()
        assert cfg.bronze_catalog_name == "bronze"
        assert cfg.bronze_warehouse == "bronze"
        assert cfg.news_table == "raw.raw_news_articles"
        assert cfg.news_lookback_hours == 6

    def test_silver_catalog_defaults(self) -> None:
        cfg = Settings()
        assert cfg.silver_catalog_name == "silver"
        assert cfg.silver_warehouse == "silver"
        assert cfg.news_digest_table == "normalized.news_clean"
        assert cfg.news_lookback_days == 3

    def test_news_top_k_default(self) -> None:
        assert Settings().news_top_k == 8

    def test_recheck_defaults(self) -> None:
        cfg = Settings()
        assert cfg.recheck_enabled is True
        assert cfg.recheck_delay_min == 20

    def test_dedup_cache_ttl_default(self) -> None:
        assert Settings().dedup_cache_ttl_sec == 900

    def test_http_port_default(self) -> None:
        assert Settings().http_port == 8081

    def test_circuit_breaker_defaults(self) -> None:
        cfg = Settings()
        assert cfg.cb_failure_threshold == 5
        assert cfg.cb_recovery_timeout_sec == 60.0

    def test_recheck_queue_max_size_default(self) -> None:
        assert Settings().recheck_queue_max_size == 1_000
