"""Format-level validation for stock tickers (Phase 4)."""
from __future__ import annotations

import pytest

from telegram_bot.domain.symbol import InvalidSymbolError, normalize_and_validate


def test_uppercases_lowercase_input() -> None:
    assert normalize_and_validate("aapl") == "AAPL"


def test_strips_whitespace() -> None:
    assert normalize_and_validate("  msft  ") == "MSFT"


def test_accepts_one_to_five_letters() -> None:
    for s in ["F", "GE", "AAPL", "GOOGL"]:
        assert normalize_and_validate(s) == s


@pytest.mark.parametrize("bad", ["", "TOOLONG", "AAPL.US", "AA1", "AA-PL", " "])
def test_rejects_invalid(bad: str) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_and_validate(bad)


def test_rejects_non_string() -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_and_validate(None)  # type: ignore[arg-type]
