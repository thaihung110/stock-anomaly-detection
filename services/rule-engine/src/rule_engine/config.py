from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_input_topic: str = "raw.stock.quotes"
    kafka_output_topic: str = "alerts.raw"

    # Iceberg REST catalog (Gravitino + Keycloak OAuth2)
    iceberg_catalog_uri: str = "http://gravitino:8090/iceberg"
    iceberg_oauth2_server_uri: str = "http://keycloak/realms/iceberg/protocol/openid-connect/token"
    iceberg_oauth2_credential: str = ""  # format: "client_id:client_secret"
    iceberg_oauth2_scope: str = "gravitino"
    iceberg_catalog_name: str = "stock_catalog"
    iceberg_warehouse: str = "gold"  # Gravitino catalog name passed as warehouse
    rule_engine_context_table: str = "gold.rule_engine_context"

    # MinIO / S3 credentials for direct object-store access from PyIceberg
    s3_endpoint: str = "http://openhouse-minio:9000"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    s3_path_style_access: bool = True

    # Rule thresholds
    price_zscore_trigger: float = 3.0
    price_zscore_high: float = 4.5
    vol_zscore_trigger: float = 3.0
    vol_zscore_high: float = 5.0
    vol_ratio_trigger: float = 3.5
    rsi_overbought: float = 80.0
    rsi_oversold: float = 20.0
    intraday_range_trigger: float = 0.05

    # PostgreSQL — individual vars assembled into DSN at startup
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "stock_anomaly"
    pg_user: str = "stock_user"
    pg_password: str = ""
    pg_dsn: str = ""

    # Telegram (for custom alert delivery)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Service
    http_port: int = 8080
    metrics_port: int = 8000

    @model_validator(mode="after")
    def build_pg_dsn(self) -> "Settings":
        if not self.pg_dsn:
            self.pg_dsn = (
                f"postgresql://{self.pg_user}:{self.pg_password}"
                f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
            )
        return self
