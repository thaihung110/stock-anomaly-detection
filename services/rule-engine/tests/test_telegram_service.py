"""Tests for telegram_service.py — send_telegram_custom_alert."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from rule_engine.config import Settings
from rule_engine.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from rule_engine.domain.models import UserAlertRule
from rule_engine.infrastructure.telegram import send_telegram_custom_alert


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg_with_telegram() -> Settings:
    return Settings(
        telegram_bot_token="test-token-abc",
        telegram_chat_id="123456789",
    )


@pytest.fixture
def cfg_without_telegram() -> Settings:
    return Settings(
        telegram_bot_token="",
        telegram_chat_id="",
    )


@pytest.fixture
def rule() -> UserAlertRule:
    return UserAlertRule(
        rule_id=uuid4(),
        user_id=uuid4(),
        symbols=["AAPL"],
        field=AlertField.PRICE,
        operator=AlertOperator.GT,
        threshold=150.0,
        frequency=AlertFrequency.EVERY_TIME,
        cooldown_min=60,
        status=AlertStatus.ACTIVE,
    )


@pytest.fixture
def triggered_at() -> datetime:
    return datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)


def _mock_http_client(side_effect: Exception | None = None) -> AsyncMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=side_effect)
    mock_client = AsyncMock()
    if side_effect and isinstance(side_effect, httpx.RequestError):
        mock_client.post = AsyncMock(side_effect=side_effect)
    else:
        mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestSendTelegramCustomAlert:
    @pytest.mark.asyncio
    async def test_skips_when_no_token(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_without_telegram: Settings
    ) -> None:
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient") as mock_cls:
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_without_telegram)
            mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_chat_id(
        self, rule: UserAlertRule, triggered_at: datetime
    ) -> None:
        cfg = Settings(telegram_bot_token="some-token", telegram_chat_id="")
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient") as mock_cls:
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg)
            mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_posts_to_telegram_api(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        mock_client = _mock_http_client()
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

        mock_client.post.assert_awaited_once()
        url = mock_client.post.call_args[0][0]
        assert "test-token-abc" in url
        assert "sendMessage" in url

    @pytest.mark.asyncio
    async def test_message_contains_symbol_and_value(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        mock_client = _mock_http_client()
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            await send_telegram_custom_alert(rule, "TSLA", 200.1234, triggered_at, cfg_with_telegram)

        payload = mock_client.post.call_args[1]["json"]
        assert "TSLA" in payload["text"]
        assert "200.1234" in payload["text"]
        assert payload["chat_id"] == "123456789"

    @pytest.mark.asyncio
    async def test_batch_daily_field_includes_warning_note(
        self, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        rsi_rule = UserAlertRule(
            rule_id=uuid4(),
            user_id=uuid4(),
            symbols=["AAPL"],
            field=AlertField.RSI_14,
            operator=AlertOperator.GT,
            threshold=80.0,
        )
        mock_client = _mock_http_client()
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            await send_telegram_custom_alert(rsi_rule, "AAPL", 85.0, triggered_at, cfg_with_telegram)

        payload = mock_client.post.call_args[1]["json"]
        assert "end-of-previous-day" in payload["text"]

    @pytest.mark.asyncio
    async def test_non_batch_field_has_no_warning_note(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        mock_client = _mock_http_client()
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

        payload = mock_client.post.call_args[1]["json"]
        assert "end-of-previous-day" not in payload["text"]

    @pytest.mark.asyncio
    async def test_connect_error_is_logged_not_raised(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        mock_client = _mock_http_client(side_effect=httpx.ConnectError("connection refused"))
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            # Must not raise
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

    @pytest.mark.asyncio
    async def test_http_status_error_is_logged_not_raised(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        mock_client = _mock_http_client(
            side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=MagicMock())
        )
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            # Must not raise
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

    @pytest.mark.asyncio
    async def test_failure_increments_metric(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        mock_client = _mock_http_client(side_effect=httpx.ConnectError("refused"))
        with patch("rule_engine.infrastructure.telegram.httpx.AsyncClient", return_value=mock_client):
            with patch("rule_engine.infrastructure.telegram.telegram_alert_send_failures_total") as mock_counter:
                await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)
                mock_counter.inc.assert_called_once()
