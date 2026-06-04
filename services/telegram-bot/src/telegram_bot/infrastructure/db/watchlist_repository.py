"""WatchlistRepository — adapter satisfying IWatchlistRepository port."""
from uuid import UUID

from telegram_bot.infrastructure.db.client import DbClient


class WatchlistRepository:
    def __init__(self, client: DbClient) -> None:
        self._client = client

    async def add(self, user_id: UUID, symbol: str) -> bool:
        return await self._client.watchlist_add(user_id, symbol)

    async def remove(self, user_id: UUID, symbol: str) -> bool:
        return await self._client.watchlist_remove(user_id, symbol)

    async def list(self, user_id: UUID) -> list[str]:
        return await self._client.watchlist_list(user_id)
