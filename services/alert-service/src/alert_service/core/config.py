from dataclasses import dataclass
from enum import Enum

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeliverySource(str, Enum):
    RAW = "raw"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class KafkaSettings:
    """Bootstrap servers, topics, and consumer groups for every Kafka handler."""

    bootstrap_servers: str
    input_topic: str
    consumer_group: str
    user_alert_topic: str
    user_consumer_group: str
    confirmed_topic: str
    confirmed_consumer_group: str
    followup_topic: str
    followup_consumer_group: str


@dataclass(frozen=True)
class TelegramSettings:
    """Bot credentials, HTTP retry policy, and proactive rate-limit budget."""

    bot_token: str
    # Accepts an integer chat_id (private chats, groups) or a @username string
    # (public channels) — int | str avoids a ValidationError when the value is
    # set to a channel username in the environment.
    chat_id: int | str
    api_base_url: str
    retry_attempts: int
    retry_base_delay: float
    # Per-replica budget. With 1 replica the global cap stays under Telegram's
    # ~30 msg/s ceiling; lower if running > 1 replica behind the same bot.
    global_rate: float
    per_chat_rate: float
    rate_limiter_cache_size: int
    rate_limiter_time_period: float


@dataclass(frozen=True)
class IcebergSettings:
    """Gravitino/REST catalog connection, table identifiers, and S3 credentials."""

    catalog_name: str
    catalog_uri: str
    oauth2_server_uri: str
    oauth2_credential: str
    oauth2_scope: str
    warehouse: str
    fact_alert_history_table: str
    # Stage D (opt-in): LLM judgement analytics table — only used when
    # delivery_source == CONFIRMED; ignored in raw mode.
    anomaly_judgement_table: str
    judgement_write_timeout_sec: float
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str
    s3_path_style_access: bool


@dataclass(frozen=True)
class PostgresSettings:
    """OLTP connection — passed individually to ``asyncpg.create_pool()``."""

    host: str
    port: int
    database: str
    user: str
    password: SecretStr


class Settings(BaseSettings):
    """Flat, env-sourced configuration.

    Fields stay flat (not nested ``BaseModel`` sub-settings) because
    pydantic-settings does not resolve per-field ``validation_alias`` on
    nested models without ``env_nested_delimiter`` — nesting the fields
    themselves would silently rename every env var the k8s manifests already
    depend on (``TELEGRAM_BOT_TOKEN`` -> ``TELEGRAM__BOT_TOKEN``). Instead,
    ``kafka`` / ``telegram`` / ``iceberg`` / ``postgres`` below are read-only
    grouped views built from these same flat fields, so code reads
    ``cfg.telegram.bot_token`` while the env var contract is untouched.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    # ADR-002: bypass LLM agent (not yet deployed) — consume from alerts.raw directly
    kafka_input_topic: str = "alerts.raw"
    kafka_consumer_group: str = "alert-service"
    kafka_user_alert_topic: str = "alerts.user"
    kafka_user_consumer_group: str = "alert-service-user"

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
    # When False (default), custom alerts go to the admin chat.
    # When True, route each custom alert to the user's own chat_id; fall back
    # to admin if chat_id is None (user has not run /start yet).
    enable_per_user_routing: bool = False
    subscriber_cache_ttl_sec: float = 60.0

    # Telegram
    telegram_bot_token: str
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
    # Stage D (opt-in): LLM judgement analytics table — only used when
    # delivery_source == CONFIRMED; ignored in raw mode.
    anomaly_judgement_table: str = "gold.anomaly_judgement"
    judgement_write_timeout_sec: float = 10.0

    # MinIO / S3 credentials for direct object-store access from PyIceberg
    s3_endpoint: str = "http://openhouse-minio:9000"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    s3_path_style_access: bool = True

    # Stage A — LLM agent integration (Bước 2)
    # delivery_source controls which Kafka topic alert-service consumes system alerts from:
    #   "raw"       — alerts.raw (default; LLM agent off or not yet deployed)
    #   "confirmed" — alerts.confirmed (LLM agent on; flip this last, rollback by reverting)
    delivery_source: DeliverySource = DeliverySource.RAW
    kafka_confirmed_topic: str = "alerts.confirmed"
    kafka_confirmed_consumer_group: str = "alert-service-confirmed"
    kafka_followup_topic: str = "alerts.followup"
    kafka_followup_consumer_group: str = "alert-service-followup"
    # When True, MEDIUM+EXPLAINED alerts are delivered only to subscribers who have
    # the symbol on their watchlist (reduces noise for already-explained anomalies).
    watchlist_gating: bool = False

    # Phase 5 — proactive rate-limit + DLQ
    telegram_global_rate: float = 25.0
    telegram_per_chat_rate: float = 1.0
    rate_limiter_cache_size: int = 10_000
    rate_limiter_time_period: float = 1.0

    dlq_enabled: bool = True
    alerts_failed_topic: str = "alerts.failed"

    # HTTP server
    app_port: int = 8080

    @property
    def kafka(self) -> KafkaSettings:
        return KafkaSettings(
            bootstrap_servers=self.kafka_bootstrap_servers,
            input_topic=self.kafka_input_topic,
            consumer_group=self.kafka_consumer_group,
            user_alert_topic=self.kafka_user_alert_topic,
            user_consumer_group=self.kafka_user_consumer_group,
            confirmed_topic=self.kafka_confirmed_topic,
            confirmed_consumer_group=self.kafka_confirmed_consumer_group,
            followup_topic=self.kafka_followup_topic,
            followup_consumer_group=self.kafka_followup_consumer_group,
        )

    @property
    def telegram(self) -> TelegramSettings:
        return TelegramSettings(
            bot_token=self.telegram_bot_token,
            chat_id=self.telegram_chat_id,
            api_base_url=self.telegram_api_base_url,
            retry_attempts=self.telegram_retry_attempts,
            retry_base_delay=self.telegram_retry_base_delay,
            global_rate=self.telegram_global_rate,
            per_chat_rate=self.telegram_per_chat_rate,
            rate_limiter_cache_size=self.rate_limiter_cache_size,
            rate_limiter_time_period=self.rate_limiter_time_period,
        )

    @property
    def iceberg(self) -> IcebergSettings:
        return IcebergSettings(
            catalog_name=self.iceberg_catalog_name,
            catalog_uri=self.iceberg_catalog_uri,
            oauth2_server_uri=self.iceberg_oauth2_server_uri,
            oauth2_credential=self.iceberg_oauth2_credential,
            oauth2_scope=self.iceberg_oauth2_scope,
            warehouse=self.iceberg_warehouse,
            fact_alert_history_table=self.fact_alert_history_table,
            anomaly_judgement_table=self.anomaly_judgement_table,
            judgement_write_timeout_sec=self.judgement_write_timeout_sec,
            s3_endpoint=self.s3_endpoint,
            s3_access_key_id=self.s3_access_key_id,
            s3_secret_access_key=self.s3_secret_access_key,
            s3_region=self.s3_region,
            s3_path_style_access=self.s3_path_style_access,
        )

    @property
    def postgres(self) -> PostgresSettings:
        return PostgresSettings(
            host=self.pg_host,
            port=self.pg_port,
            database=self.pg_database,
            user=self.pg_user,
            password=self.pg_password,
        )
