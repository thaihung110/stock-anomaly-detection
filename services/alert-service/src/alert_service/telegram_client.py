"""Async Telegram Bot API client with retry, timeout, and 429 Retry-After handling."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final

import httpx
import structlog

from alert_service.config import Settings

logger = structlog.get_logger(__name__)

_MAX_RETRY_AFTER_SECONDS: Final[float] = 60.0


class TelegramError(Exception):
    """Base class for Telegram delivery failures (transient, retried-then-failed)."""


class TelegramRateLimitError(TelegramError):
    """Raised when retries are exhausted after repeated HTTP 429 responses."""


class TelegramPermanentError(TelegramError):
    """Raised on non-retriable 4xx responses (bad token, chat not found, etc.)."""


@dataclass(frozen=True)
class TelegramConfig:
    """Immutable client configuration sourced from per-service ``Settings``."""

    bot_token: str
    api_base_url: str = "https://api.telegram.org"
    retry_attempts: int = 3
    retry_base_delay: float = 1.0
    request_timeout: float = 10.0


_Transport = httpx.AsyncBaseTransport | None


class SharedTelegramClient:
    """Stateless-per-call Telegram client. ``chat_id`` is passed per send."""

    def __init__(self, cfg: TelegramConfig, *, transport: _Transport = None) -> None:
        if not cfg.bot_token:
            raise ValueError("bot_token must be non-empty")
        if cfg.retry_attempts < 1:
            raise ValueError("retry_attempts must be >= 1")
        self._cfg = cfg
        self._transport = transport
        self._send_url = f"{cfg.api_base_url.rstrip('/')}/bot{cfg.bot_token}/sendMessage"

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        parse_mode: str | None = "Markdown",
    ) -> None:
        """Send a message, retrying transient failures with exponential backoff.

        Raises:
            TelegramPermanentError: non-retriable 4xx (≠ 429).
            TelegramRateLimitError: 429 exhausted all retries.
            TelegramError: timeout / 5xx / network error exhausted all retries.
        """
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        last_exc: Exception | None = None
        last_status: int | None = None

        for attempt in range(self._cfg.retry_attempts):
            try:
                async with self._build_client() as client:
                    resp = await client.post(self._send_url, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                await self._sleep_backoff(attempt, reason="transport_error", error=str(exc))
                continue

            if resp.status_code < 400:
                logger.info(
                    "telegram_message_sent",
                    chat_id=chat_id,
                    attempt=attempt,
                    status=resp.status_code,
                )
                return

            last_status = resp.status_code

            if resp.status_code == 429:
                delay = _parse_retry_after(resp) or self._backoff_delay(attempt)
                logger.warning(
                    "telegram_rate_limited",
                    chat_id=chat_id,
                    attempt=attempt,
                    retry_in=delay,
                )
                if attempt < self._cfg.retry_attempts - 1:
                    await asyncio.sleep(delay)
                continue

            if 400 <= resp.status_code < 500:
                logger.error(
                    "telegram_permanent_error",
                    chat_id=chat_id,
                    status=resp.status_code,
                    body=resp.text[:512],
                )
                raise TelegramPermanentError(
                    f"Telegram returned {resp.status_code}: {resp.text[:256]}"
                )

            last_exc = httpx.HTTPStatusError(
                f"server error {resp.status_code}", request=resp.request, response=resp
            )
            await self._sleep_backoff(attempt, reason="server_error", status=resp.status_code)

        if last_status == 429:
            raise TelegramRateLimitError(
                f"Telegram rate-limited after {self._cfg.retry_attempts} attempts"
            ) from last_exc
        raise TelegramError(
            f"Telegram send failed after {self._cfg.retry_attempts} attempts"
        ) from last_exc

    def _build_client(self) -> httpx.AsyncClient:
        if self._transport is not None:
            return httpx.AsyncClient(timeout=self._cfg.request_timeout, transport=self._transport)
        return httpx.AsyncClient(timeout=self._cfg.request_timeout)

    def _backoff_delay(self, attempt: int) -> float:
        return self._cfg.retry_base_delay * (2**attempt)

    async def _sleep_backoff(self, attempt: int, **log_ctx: object) -> None:
        if attempt >= self._cfg.retry_attempts - 1:
            return
        delay = self._backoff_delay(attempt)
        logger.warning("telegram_send_retry", attempt=attempt, retry_in=delay, **log_ctx)
        await asyncio.sleep(delay)


def _parse_retry_after(resp: httpx.Response) -> float | None:
    """Read ``Retry-After`` header or Telegram JSON ``parameters.retry_after``."""
    header = resp.headers.get("Retry-After")
    if header is not None:
        try:
            return min(float(header), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        params = body.get("parameters")
        if isinstance(params, dict):
            retry_after = params.get("retry_after")
            if isinstance(retry_after, (int, float)):
                return min(float(retry_after), _MAX_RETRY_AFTER_SECONDS)
    return None


def build_telegram_client(cfg: Settings) -> SharedTelegramClient:
    """Construct a ``SharedTelegramClient`` from service ``Settings``."""
    return SharedTelegramClient(
        TelegramConfig(
            bot_token=cfg.telegram_bot_token,
            api_base_url=cfg.telegram_api_base_url,
            retry_attempts=cfg.telegram_retry_attempts,
            retry_base_delay=cfg.telegram_retry_base_delay,
        )
    )


__all__ = [
    "SharedTelegramClient",
    "TelegramConfig",
    "TelegramError",
    "TelegramPermanentError",
    "TelegramRateLimitError",
    "build_telegram_client",
]
