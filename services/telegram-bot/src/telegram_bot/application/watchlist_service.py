"""WatchlistService — manages per-user symbol subscriptions.

After any mutation we POST /internal/reload-subscribers so the alert-service's
TTL cache is invalidated immediately rather than waiting up to 60s.
"""
import structlog

from telegram_bot.domain.ports import (
    IAlertRepository,
    IAlertServiceClient,
    IWatchlistRepository,
)
from telegram_bot.domain.symbol import normalize_and_validate

logger = structlog.get_logger(__name__)


class WatchlistService:
    def __init__(
        self,
        repo: IWatchlistRepository,
        user_repo: IAlertRepository,
        alert_client: IAlertServiceClient,
    ) -> None:
        self._repo = repo
        self._user_repo = user_repo
        self._alert_client = alert_client

    async def watch(self, telegram_id: int, chat_id: int, raw_symbol: str) -> bool:
        symbol = normalize_and_validate(raw_symbol)
        # upsert_chat_id ensures chat_id is set so alert-service can deliver
        user_id = await self._user_repo.upsert_chat_id(telegram_id, chat_id)
        added = await self._repo.add(user_id, symbol)
        if added:
            await self._alert_client.reload_subscribers()
        logger.info("watchlist_add", telegram_id=telegram_id, symbol=symbol, added=added)
        return added

    async def unwatch(self, telegram_id: int, chat_id: int, raw_symbol: str) -> bool:
        symbol = normalize_and_validate(raw_symbol)
        user_id = await self._user_repo.upsert_chat_id(telegram_id, chat_id)
        removed = await self._repo.remove(user_id, symbol)
        if removed:
            await self._alert_client.reload_subscribers()
        logger.info("watchlist_remove", telegram_id=telegram_id, symbol=symbol, removed=removed)
        return removed

    async def list_watchlist(self, telegram_id: int) -> list[str]:
        user_id = await self._user_repo.get_or_create_user(telegram_id)
        return await self._repo.list(user_id)
