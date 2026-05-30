"""Custom user alert evaluation — manages rule cache, cooldowns, and prev-value tracking."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import structlog

from rule_engine.config import Settings
from rule_engine.domain.custom_rules import evaluate_condition, get_field_value
from rule_engine.domain.enums import AlertField, AlertFrequency
from rule_engine.domain.models import UserAlertEvent, UserAlertRule
from rule_engine.domain.schema import QuoteEvent
from rule_engine.infrastructure.db.repository import UserAlertRepository
from rule_engine.infrastructure.telegram import send_telegram_custom_alert

logger = structlog.get_logger(__name__)

_SECONDS_PER_MINUTE = 60


class UserAlertProcessor:
    """Evaluates custom user alert rules against quote events.

    Owns all in-memory state needed for custom rule evaluation:
    - Active rules cache (reloaded on demand)
    - Cooldown tracking per (rule_id, symbol)
    - Previous-value tracking for CROSSES_UP/CROSSES_DOWN operators

    All shared state is protected by asyncio locks.
    """

    def __init__(self, repository: UserAlertRepository, cfg: Settings) -> None:
        self._repository = repository
        self._cfg = cfg

        self._rules_cache: list[UserAlertRule] = []
        self._rules_lock = asyncio.Lock()

        self._last_fired: dict[tuple[UUID, str], datetime] = {}
        self._last_fired_lock = asyncio.Lock()

        self._prev_values: dict[tuple[str, AlertField], float] = {}
        self._prev_values_lock = asyncio.Lock()

        # Keeps strong references to fire-and-forget Telegram tasks so the GC
        # doesn't destroy them before they complete.
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def reload_rules(self) -> int:
        """Reload active rules from the database. Returns count of loaded rules."""
        rules = await self._repository.get_active_rules()
        async with self._rules_lock:
            self._rules_cache = rules
        return len(rules)

    async def evaluate(self, event: QuoteEvent, ctx: dict[str, float] | None) -> None:
        """Evaluate all active custom rules against the given quote event."""
        now = datetime.now(UTC)

        async with self._rules_lock:
            rules_snapshot = list(self._rules_cache)

        async with self._prev_values_lock:
            prev_snapshot = dict(self._prev_values)

        for rule in rules_snapshot:
            await self._evaluate_one(event, ctx, rule, now, prev_snapshot)

    async def update_prev_values(
        self, event: QuoteEvent, ctx: dict[str, float] | None
    ) -> None:
        """Update previous-value cache after processing a quote.

        Must be called after evaluate() to ensure crossing-detection compares
        against the value from the *previous* quote, not the current one.
        """
        updates: dict[tuple[str, AlertField], float] = {}
        for field in AlertField:
            val = get_field_value(event, field, ctx)
            if val is not None:
                updates[(event.symbol, field)] = val

        async with self._prev_values_lock:
            self._prev_values.update(updates)

    async def _evaluate_one(
        self,
        event: QuoteEvent,
        ctx: dict[str, float] | None,
        rule: UserAlertRule,
        now: datetime,
        prev_snapshot: dict[tuple[str, AlertField], float],
    ) -> None:
        if rule.symbols != ["*"] and event.symbol not in rule.symbols:
            return

        current = get_field_value(event, rule.field, ctx)
        if current is None:
            return

        prev = prev_snapshot.get((event.symbol, rule.field))
        if not evaluate_condition(current, rule.operator, rule.threshold, prev):
            return

        if rule.rule_id is None:
            logger.error("skipping_rule_with_null_id", user_id=str(rule.user_id))
            return

        if await self._check_cooldown(rule.rule_id, event.symbol, now, rule.cooldown_min):
            return

        alert_event = UserAlertEvent(
            rule_id=rule.rule_id,
            user_id=rule.user_id,
            symbol=event.symbol,
            triggered_at=now,
            field_snapshot=rule.field,
            operator_snapshot=rule.operator,
            threshold_snapshot=rule.threshold,
            triggered_value=current,
        )
        try:
            await self._repository.insert_event(alert_event)
        except asyncpg.PostgresError as exc:
            logger.error(
                "failed_to_insert_alert_event",
                rule_id=str(rule.rule_id),
                user_id=str(rule.user_id),
                symbol=event.symbol,
                error=str(exc),
            )
            # Do NOT record cooldown — the event was not persisted, so the rule
            # must be eligible to retry on the next quote.
            return

        # Record fired only after the event is durably persisted.
        await self._record_fired(rule.rule_id, event.symbol, now)

        logger.info(
            "custom_alert_fired",
            symbol=event.symbol,
            rule_id=str(rule.rule_id),
            field=rule.field.value,
            operator=rule.operator.value,
            threshold=rule.threshold,
            value=current,
        )

        # Deliver via Telegram off the hot path — the event log is already written.
        # send_telegram_custom_alert handles all its own exceptions internally.
        task = asyncio.create_task(
            send_telegram_custom_alert(rule, event.symbol, current, now, self._cfg),
            name=f"telegram_alert_{rule.rule_id}_{event.symbol}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        if rule.frequency == AlertFrequency.ONCE:
            await self._mark_triggered(rule.rule_id)

    async def _check_cooldown(
        self, rule_id: UUID, symbol: str, now: datetime, cooldown_min: int
    ) -> bool:
        """Return True if the rule is still within its cooldown window (should skip)."""
        key: tuple[UUID, str] = (rule_id, symbol)
        async with self._last_fired_lock:
            last = self._last_fired.get(key)
            return bool(last and (now - last).total_seconds() < cooldown_min * _SECONDS_PER_MINUTE)

    async def _record_fired(self, rule_id: UUID, symbol: str, now: datetime) -> None:
        """Record that the rule fired at `now`. Must be called only after a successful INSERT."""
        async with self._last_fired_lock:
            self._last_fired[(rule_id, symbol)] = now

    async def _mark_triggered(self, rule_id: UUID) -> None:
        try:
            await self._repository.mark_triggered(rule_id)
        except asyncpg.PostgresError as exc:
            logger.error(
                "failed_to_mark_rule_triggered",
                rule_id=str(rule_id),
                error=str(exc),
            )
            return

        async with self._rules_lock:
            self._rules_cache = [r for r in self._rules_cache if r.rule_id != rule_id]
