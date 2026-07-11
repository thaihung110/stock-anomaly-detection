"""Shared runtime state for alert-service, injected via FastAPI ``Depends()``.

Phase 2 refactor: replaces the six separate module-level globals
(``_telegram``, ``_pg_pool``, ``_cache``, ``_delivery``, ``_rate_limiter``,
``_dlq``) that ``main.py`` used to mutate one-by-one via the ``global``
keyword. There is now exactly one mutable object, populated once in
``lifespan()`` and stored on ``app.state.container`` — every router/consumer
module reads from it instead of importing loose module attributes, and tests
can construct a ``Container`` with fakes instead of monkeypatching globals.
"""
from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from alert_service.infrastructure.dlq_producer import DLQPublisher
from alert_service.infrastructure.iceberg.history_writer import HistoryWriter
from alert_service.infrastructure.iceberg.judgement_writer import JudgementWriter
from alert_service.infrastructure.telegram_client import SharedTelegramClient
from alert_service.services.delivery import AlertDeliveryService
from alert_service.services.rate_limiter import PerChatRateLimiter
from alert_service.services.subscriber_cache import SubscriberCache


@dataclass
class Container:
    """Populated once by ``main.lifespan()``; read-only after startup.

    ``history_writer`` / ``judgement_writer`` are constructed eagerly (they
    have no external dependencies) so ``lifespan()`` only needs to call
    ``.init(cfg)`` on them. Everything else is only known once startup runs
    (e.g. ``telegram`` needs the bot token, ``cache`` needs a DB pool) so it
    stays ``None`` until then.
    """

    history_writer: HistoryWriter
    judgement_writer: JudgementWriter
    telegram: SharedTelegramClient | None = None
    pg_pool: asyncpg.Pool | None = None
    cache: SubscriberCache | None = None
    delivery: AlertDeliveryService | None = None
    rate_limiter: PerChatRateLimiter | None = None
    dlq: DLQPublisher | None = None

    def require_delivery(self) -> AlertDeliveryService:
        """Return ``delivery``, raising if a handler fires before ``lifespan()`` ran.

        Used instead of ``assert delivery is not None`` because ``assert`` is
        removed entirely under ``python -O`` / ``PYTHONOPTIMIZE=1`` — this
        invariant must hold in every build, optimized or not.
        """
        if self.delivery is None:
            raise RuntimeError("lifespan() must run before any handler fires")
        return self.delivery
