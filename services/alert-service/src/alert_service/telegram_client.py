import asyncio

import httpx
import structlog

from alert_service.config import Settings

logger = structlog.get_logger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, cfg: Settings) -> None:
        self._token = cfg.telegram_bot_token
        self._chat_id = cfg.telegram_chat_id
        self._base_url = cfg.telegram_api_base_url
        self._retry_attempts = cfg.telegram_retry_attempts
        self._retry_base_delay = cfg.telegram_retry_base_delay

    async def send_message(self, text: str) -> None:
        url = f"{self._base_url}/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"}

        last_exc: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    logger.info("telegram_message_sent", attempt=attempt)
                    return
            except httpx.HTTPError as exc:
                last_exc = exc
                delay = self._retry_base_delay * (2**attempt)
                logger.warning(
                    "telegram_send_failed",
                    attempt=attempt,
                    error=str(exc),
                    retry_in=delay,
                )
                if attempt < self._retry_attempts - 1:
                    await asyncio.sleep(delay)

        raise TelegramError(f"Failed after {self._retry_attempts} attempts") from last_exc
