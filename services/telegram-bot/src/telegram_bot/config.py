from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_APP_PORT = 8080
_DEFAULT_WEBHOOK_PATH = "/webhook"


class Settings(BaseSettings):
    """Service configuration loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str
    webhook_host: str  # e.g. "https://bot.example.com"
    webhook_path: str = _DEFAULT_WEBHOOK_PATH
    app_port: int = _DEFAULT_APP_PORT
    pg_dsn: str                  # postgresql://user:pass@host:5432/dbname
    rule_engine_url: str         # e.g. "http://rule-engine-svc:8000"

    @property
    def webhook_url(self) -> str:
        """Full webhook URL registered with the Telegram Bot API."""
        return f"{self.webhook_host}{self.webhook_path}"
