"""Stock symbol normalization + format validation.

Phase 4: format-only validation (regex). Tighter whitelist against ``dim_symbol``
is deferred to Phase 5 to avoid coupling the telegram-bot service to Iceberg.
"""
import re

_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")


class InvalidSymbolError(ValueError):
    """Raised when a user-supplied ticker fails format validation."""


def normalize_and_validate(raw: str) -> str:
    """Uppercase + format-check a ticker. Raises InvalidSymbolError on failure."""
    if not raw or not isinstance(raw, str):
        raise InvalidSymbolError("symbol must be a non-empty string")
    candidate = raw.strip().upper()
    if not _SYMBOL_RE.match(candidate):
        raise InvalidSymbolError(
            f"invalid symbol {raw!r}: expected 1-5 uppercase letters, e.g. AAPL"
        )
    return candidate
