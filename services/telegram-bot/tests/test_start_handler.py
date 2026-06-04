"""Unit tests for the /start handler — chat_id UPSERT + private-chat guard."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from telegram_bot.infrastructure.telegram.handlers.start import _make_start_handler


def _make_update(*, chat_type: str = "private", chat_id: int = 555, tg_id: int = 42):
    msg = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(
        effective_message=msg,
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=tg_id, first_name="Test"),
    ), msg


@pytest.mark.asyncio
async def test_start_in_private_chat_upserts_chat_id() -> None:
    repo = AsyncMock()
    repo.upsert_chat_id.return_value = uuid4()
    handler = _make_start_handler(repo)

    update, msg = _make_update(chat_type="private", chat_id=555, tg_id=42)
    await handler(update, SimpleNamespace())

    repo.upsert_chat_id.assert_awaited_once_with(42, 555)
    msg.reply_text.assert_awaited_once()
    assert msg.reply_text.await_args.kwargs.get("parse_mode") == "Markdown"


@pytest.mark.asyncio
async def test_start_in_group_chat_blocks_and_skips_upsert() -> None:
    repo = AsyncMock()
    handler = _make_start_handler(repo)

    update, msg = _make_update(chat_type="group")
    await handler(update, SimpleNamespace())

    repo.upsert_chat_id.assert_not_awaited()
    msg.reply_text.assert_awaited_once()
    body = msg.reply_text.await_args.args[0]
    assert "private" in body.lower()
