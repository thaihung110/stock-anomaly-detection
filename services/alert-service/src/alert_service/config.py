from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_input_topic: str = "alerts.confirmed"
    kafka_consumer_group: str = "alert-service"

    # PostgreSQL — passed individually to asyncpg.create_pool() so the
    # password stays inside SecretStr and never leaks into a plain string field
    # (logs, repr, error messages).
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "stock_anomaly"
    pg_user: str = "stock_user"
    pg_password: SecretStr = SecretStr("")

    # Phase 3 — fan-out config
    # When False (default), system alerts go only to the admin chat (legacy
    # behavior). When True, fan out to all matching subscribers based on
    # user_preferences.system_alert_mode and user_watchlist membership.
    enable_fanout: bool = False
    subscriber_cache_ttl_sec: float = 60.0

    # Telegram
    telegram_bot_token: str
    # Accepts an integer chat_id (private chats, groups) or a @username string
    # (public channels).  Using int | str avoids a ValidationError when the
    # value is set to a channel username in the environment.
    telegram_chat_id: int | str
    telegram_api_base_url: str = "https://api.telegram.org"
    telegram_retry_attempts: int = 3
    telegram_retry_base_delay: float = 1.0  # seconds, doubled each retry

    # Iceberg / Gravitino
    iceberg_catalog_name: str = "stock_catalog"
    iceberg_catalog_uri: str = "http://openhouse-gravitino:9001/iceberg/"
    iceberg_oauth2_server_uri: str = "http://openhouse-keycloak/realms/iceberg/protocol/openid-connect/token"
    iceberg_oauth2_credential: str = ""
    iceberg_oauth2_scope: str = "gravitino"
    iceberg_warehouse: str = "gold"
    fact_alert_history_table: str = "gold.fact_alert_history"

    # MinIO / S3 credentials for direct object-store access from PyIceberg
    s3_endpoint: str = "http://openhouse-minio:9000"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    s3_path_style_access: bool = True

    # Phase 5 — proactive rate-limit + DLQ
    # Per-replica budget. With 1 replica the global cap stays under Telegram's
    # ~30 msg/s ceiling; lower if running > 1 replica behind the same bot.
    telegram_global_rate: float = 25.0
    telegram_per_chat_rate: float = 1.0
    rate_limiter_cache_size: int = 10_000
    rate_limiter_time_period: float = 1.0

    dlq_enabled: bool = True
    alerts_failed_topic: str = "alerts.failed"

    # HTTP server
    app_port: int = 8080
