from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_input_topic: str = "alerts.raw"
    kafka_consumer_group: str = "alert-service"

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str
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

    # HTTP server
    app_port: int = 8080
