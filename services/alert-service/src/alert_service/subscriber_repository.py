"""Read-only PostgreSQL queries for system-alert subscribers.

A "subscriber" is a row in ``users`` whose ``user_preferences.system_alert_mode``
either includes every symbol (``ALL``) or includes the alert's specific symbol
via ``user_watchlist`` (``WATCHLIST_ONLY``). Users with ``system_alert_mode = 'OFF'``
or a NULL ``chat_id`` are excluded — they cannot receive a Telegram message.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Subscriber:
    """One recipient of a system alert for a given symbol."""

    user_id: UUID
    chat_id: int


class SubscriberRepository:
    """Fetches the recipient list for a symbol from PostgreSQL.

    The query relies on indexes added in migration ``002_multi_user_routing.sql``
    (``idx_users_chat_id`` partial, ``idx_watchlist_symbol``) so a cache miss
    stays sub-millisecond even with thousands of users.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_subscribers_for_symbol(self, symbol: str) -> list[Subscriber]:
        """Return the subscribers for ``symbol``.

        Args:
            symbol: Uppercase ticker, e.g. ``"AAPL"``.

        Returns:
            Subscribers with a non-NULL ``chat_id``. Order is undefined.
        """
        rows = await self._pool.fetch(
            """
            SELECT u.user_id, u.chat_id
            FROM users u
            JOIN user_preferences p ON p.user_id = u.user_id
            WHERE u.chat_id IS NOT NULL
              AND (
                    p.system_alert_mode = 'ALL'
                 OR (p.system_alert_mode = 'WATCHLIST_ONLY' AND EXISTS (
                        SELECT 1 FROM user_watchlist w
                        WHERE w.user_id = u.user_id AND w.symbol = $1
                    ))
              )
            """,
            symbol.upper(),
        )
        subs = [Subscriber(user_id=r["user_id"], chat_id=r["chat_id"]) for r in rows]
        logger.debug("subscribers_loaded", symbol=symbol, count=len(subs))
        return subs
