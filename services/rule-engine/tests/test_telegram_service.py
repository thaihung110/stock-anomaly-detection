"""Tests for send_telegram_custom_alert.

Phase 1: HTTP delivery is delegated to the shared ``telegram_client``
package, so tests patch the ``_get_client`` collaborator rather than
reaching into ``httpx`` internals.
"""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from telegram_client import TelegramError

from rule_engine.config import Settings
from rule_engine.domain.enums import AlertField, AlertFrequency, AlertOperator, AlertStatus
from rule_engine.domain.models import UserAlertRule
from rule_engine.infrastructure import telegram as telegram_mod
from rule_engine.infrastructure.telegram import send_telegram_custom_alert


@pytest.fixture(autouse=True)
def _reset_client_cache() -> None:
    telegram_mod._client_cache = None
    telegram_mod._client_cache_key = None


@pytest.fixture
def cfg_with_telegram() -> Settings:
    return Settings(telegram_bot_token="test-token-abc", telegram_chat_id="123456789")


@pytest.fixture
def cfg_without_telegram() -> Settings:
    return Settings(telegram_bot_token="", telegram_chat_id="")


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


def _stub_client(send: AsyncMock) -> object:
    class _Stub:
        send_message = send

    return _Stub()


class TestSendTelegramCustomAlert:
    @pytest.mark.asyncio
    async def test_skips_when_no_token(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_without_telegram: Settings
    ) -> None:
        with patch.object(telegram_mod, "_get_client") as mock_get:
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_without_telegram)
            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_chat_id(
        self, rule: UserAlertRule, triggered_at: datetime
    ) -> None:
        cfg = Settings(telegram_bot_token="some-token", telegram_chat_id="")
        with patch.object(telegram_mod, "_get_client") as mock_get:
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg)
            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_posts_to_telegram_api(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        send = AsyncMock()
        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

        send.assert_awaited_once()
        chat_id, text = send.await_args.args[0], send.await_args.args[1]
        assert chat_id == "123456789"
        assert "AAPL" in text

    @pytest.mark.asyncio
    async def test_message_contains_symbol_and_value(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        send = AsyncMock()
        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "TSLA", 200.1234, triggered_at, cfg_with_telegram)

        text = send.await_args.args[1]
        assert "TSLA" in text
        assert "200.1234" in text

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
        send = AsyncMock()
        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rsi_rule, "AAPL", 85.0, triggered_at, cfg_with_telegram)

        assert "end-of-previous-day" in send.await_args.args[1]

    @pytest.mark.asyncio
    async def test_non_batch_field_has_no_warning_note(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        send = AsyncMock()
        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

        assert "end-of-previous-day" not in send.await_args.args[1]

    @pytest.mark.asyncio
    async def test_telegram_error_is_logged_not_raised(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        send = AsyncMock(side_effect=TelegramError("boom"))
        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(
        self, rule: UserAlertRule, triggered_at: datetime, cfg_with_telegram: Settings
    ) -> None:
        send = AsyncMock(side_effect=TelegramError("refused"))
        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_with_telegram)


# ── Phase 2 — Per-user routing ────────────────────────────────────────────────


class TestPerUserRouting:
    """Verify ``enable_per_user_routing`` correctly switches between
    per-user delivery (``users.chat_id``) and the admin fallback chat.
    """

    @pytest.fixture
    def cfg_routing_on(self) -> Settings:
        return Settings(
            telegram_bot_token="t",
            telegram_chat_id="ADMIN_CHAT",
            enable_per_user_routing=True,
        )

    @pytest.fixture
    def cfg_routing_off(self) -> Settings:
        return Settings(
            telegram_bot_token="t",
            telegram_chat_id="ADMIN_CHAT",
            enable_per_user_routing=False,
        )

    @staticmethod
    def _rule(chat_id: int | None) -> UserAlertRule:
        return UserAlertRule(
            rule_id=uuid4(),
            user_id=uuid4(),
            symbols=["AAPL"],
            field=AlertField.PRICE,
            operator=AlertOperator.GT,
            threshold=150.0,
            chat_id=chat_id,
        )

    @pytest.mark.asyncio
    async def test_two_users_get_two_distinct_chats(
        self, triggered_at: datetime, cfg_routing_on: Settings
    ) -> None:
        send = AsyncMock()
        rule_a = self._rule(chat_id=1001)
        rule_b = self._rule(chat_id=2002)

        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule_a, "AAPL", 155.0, triggered_at, cfg_routing_on)
            await send_telegram_custom_alert(rule_b, "AAPL", 155.0, triggered_at, cfg_routing_on)

        chat_ids = [call.args[0] for call in send.await_args_list]
        assert chat_ids == [1001, 2002]

    @pytest.mark.asyncio
    async def test_null_chat_id_falls_back_to_admin(
        self, triggered_at: datetime, cfg_routing_on: Settings
    ) -> None:
        send = AsyncMock()
        rule = self._rule(chat_id=None)

        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_routing_on)

        assert send.await_args.args[0] == "ADMIN_CHAT"

    @pytest.mark.asyncio
    async def test_routing_disabled_always_uses_admin_chat(
        self, triggered_at: datetime, cfg_routing_off: Settings
    ) -> None:
        send = AsyncMock()
        # Even with a populated chat_id, the flag pins delivery to admin.
        rule = self._rule(chat_id=9999)

        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_routing_off)

        assert send.await_args.args[0] == "ADMIN_CHAT"

    @pytest.mark.asyncio
    async def test_routing_on_with_chat_id_uses_user_chat(
        self, triggered_at: datetime, cfg_routing_on: Settings
    ) -> None:
        send = AsyncMock()
        rule = self._rule(chat_id=1001)

        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg_routing_on)

        assert send.await_args.args[0] == 1001

    @pytest.mark.asyncio
    async def test_no_admin_and_no_user_chat_skips_silently(
        self, triggered_at: datetime
    ) -> None:
        cfg = Settings(
            telegram_bot_token="t",
            telegram_chat_id="",
            enable_per_user_routing=True,
        )
        rule = self._rule(chat_id=None)
        send = AsyncMock()

        with patch.object(telegram_mod, "_get_client", return_value=_stub_client(send)):
            await send_telegram_custom_alert(rule, "AAPL", 155.0, triggered_at, cfg)

        send.assert_not_awaited()
