"""Unit tests for ``SharedTelegramClient`` using ``httpx.MockTransport``."""
from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from alert_service.telegram_client import (
    SharedTelegramClient,
    TelegramConfig,
    TelegramError,
    TelegramPermanentError,
    TelegramRateLimitError,
)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    attempts: int = 3,
    base_delay: float = 0.0,
) -> SharedTelegramClient:
    cfg = TelegramConfig(
        bot_token="TEST_TOKEN",
        retry_attempts=attempts,
        retry_base_delay=base_delay,
    )
    return SharedTelegramClient(cfg, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_send_message_success() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    await client.send_message(chat_id=12345, text="hello")

    assert len(calls) == 1
    assert calls[0].url.path.endswith("/sendMessage")


@pytest.mark.asyncio
async def test_send_message_retries_on_5xx_then_succeeds() -> None:
    statuses = iter([500, 502, 200])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json={"ok": True})

    client = _client(handler)
    await client.send_message(chat_id=1, text="t")


@pytest.mark.asyncio
async def test_send_message_5xx_exhausts_retries() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = _client(handler, attempts=2)
    with pytest.raises(TelegramError) as exc_info:
        await client.send_message(chat_id=1, text="t")
    assert not isinstance(exc_info.value, TelegramRateLimitError)


@pytest.mark.asyncio
async def test_429_honours_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("alert_service.telegram_client.asyncio.sleep", fake_sleep)

    statuses = iter([429, 200])

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 429:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    await client.send_message(chat_id=1, text="t")

    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_429_honours_telegram_body_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("alert_service.telegram_client.asyncio.sleep", fake_sleep)

    statuses = iter([429, 200])

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        if status == 429:
            return httpx.Response(
                429,
                json={"ok": False, "error_code": 429, "parameters": {"retry_after": 4}},
            )
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    await client.send_message(chat_id=1, text="t")

    assert sleeps == [4.0]


@pytest.mark.asyncio
async def test_429_exhausted_raises_rate_limit_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    client = _client(handler, attempts=2)
    with pytest.raises(TelegramRateLimitError):
        await client.send_message(chat_id=1, text="t")


@pytest.mark.asyncio
async def test_permanent_4xx_not_retried() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, text="Bad Request: chat not found")

    client = _client(handler, attempts=3)
    with pytest.raises(TelegramPermanentError):
        await client.send_message(chat_id=1, text="t")

    assert calls == 1


@pytest.mark.asyncio
async def test_timeout_is_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler, attempts=3)
    await client.send_message(chat_id=1, text="t")
    assert attempts == 2


@pytest.mark.asyncio
async def test_empty_bot_token_rejected() -> None:
    with pytest.raises(ValueError):
        SharedTelegramClient(TelegramConfig(bot_token=""))


@pytest.mark.asyncio
async def test_zero_retry_attempts_rejected() -> None:
    with pytest.raises(ValueError):
        SharedTelegramClient(TelegramConfig(bot_token="t", retry_attempts=0))
