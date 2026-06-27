from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_input_topic: str = "alerts.raw"
    kafka_consumer_group: str = "llm-agent"
    kafka_output_topic: str = "alerts.confirmed"
    kafka_followup_topic: str = "alerts.followup"

    # LLM — format: "provider:model"
    # Examples: "google_genai:gemini-2.5-flash-lite", "openai:gpt-4o-mini",
    #            "anthropic:claude-haiku-4-5"
    # Changing provider = change this env only, no code change.
    llm_model: str = "google_genai:gemini-2.5-flash-lite"
    llm_escalation_model: str = ""  # optional; used for HIGH severity / ambiguous
    google_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    # Deadline for full agent pipeline; exceeded → UNCERTAIN (fail-open)
    agent_ttl_sec: float = 8.0

    # Iceberg REST catalog (shared Gravitino URI + OAuth2; warehouse differs per layer)
    iceberg_catalog_uri: str = "http://gravitino:8090/iceberg"
    iceberg_oauth2_server_uri: str = (
        "http://keycloak/realms/iceberg/protocol/openid-connect/token"
    )
    iceberg_oauth2_credential: str = ""  # format: "client_id:client_secret"
    iceberg_oauth2_scope: str = "gravitino"

    # Bronze catalog (warehouse="bronze") — raw.raw_news_articles — fresh tail
    bronze_catalog_name: str = "bronze"
    bronze_warehouse: str = "bronze"
    news_table: str = "raw.raw_news_articles"
    news_lookback_hours: int = 6

    # Silver catalog (warehouse="silver") — normalized.news_clean — historical body
    silver_catalog_name: str = "silver"
    silver_warehouse: str = "silver"
    news_digest_table: str = "normalized.news_clean"
    news_lookback_days: int = 3

    # Union result: top-K articles sent to LLM prompt
    news_top_k: int = 8

    # MinIO / S3 (client-side access, no credential vending)
    s3_endpoint: str = "http://openhouse-minio:9000"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    s3_path_style_access: bool = True

    # Follow-up re-check (Bước 8)
    recheck_enabled: bool = True
    recheck_delay_min: int = 20
    recheck_queue_max_size: int = 1_000

    # Idempotency: skip reprocessing same alert_id within this window
    dedup_cache_ttl_sec: int = 900

    # Circuit breaker: trip after this many consecutive LLM failures
    cb_failure_threshold: int = 5
    # Seconds to stay OPEN before probing again (HALF_OPEN)
    cb_recovery_timeout_sec: float = 60.0

    # Service HTTP port
    http_port: int = 8081
