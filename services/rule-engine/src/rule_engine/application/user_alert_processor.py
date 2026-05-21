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
from rule_engine.metrics import (
    custom_alerts_fired_total,
    custom_rules_evaluated_total,
    db_insert_failures_total,
)

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
        updates: dict[tuple[str, AlertField], float] = {
            (event.symbol, AlertField.PRICE): event.price,
            (event.symbol, AlertField.DAILY_RETURN): event.change_pct,
            (event.symbol, AlertField.DAY_VOLUME): float(event.day_volume),
        }
        if ctx:
            for field, key in (
                (AlertField.PRICE_ZSCORE, "price_zscore"),
                (AlertField.VOLUME_ZSCORE, "volume_zscore"),
                (AlertField.VOLUME_RATIO_20D, "vol_ratio_20d"),
                (AlertField.RSI_14, "rsi_14"),
                (AlertField.BB_POSITION, "bb_position"),
            ):
                val = ctx.get(key)
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

        custom_rules_evaluated_total.inc()

        prev = prev_snapshot.get((event.symbol, rule.field))
        if not evaluate_condition(current, rule.operator, rule.threshold, prev):
            return

        if rule.rule_id is None:
            logger.error("skipping_rule_with_null_id", user_id=str(rule.user_id))
            return

        if await self._in_cooldown(rule.rule_id, event.symbol, now, rule.cooldown_min):
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
        await self._repository.insert_event(alert_event)
        custom_alerts_fired_total.labels(
            field=rule.field.value, operator=rule.operator.value
        ).inc()
        logger.info(
            "custom_alert_fired",
            symbol=event.symbol,
            rule_id=str(rule.rule_id),
            field=rule.field.value,
            operator=rule.operator.value,
            threshold=rule.threshold,
            value=current,
        )

        await send_telegram_custom_alert(rule, event.symbol, current, now, self._cfg)

        if rule.frequency == AlertFrequency.ONCE:
            await self._mark_triggered(rule.rule_id)

    async def _in_cooldown(
        self, rule_id: UUID, symbol: str, now: datetime, cooldown_min: int
    ) -> bool:
        """Returns True if in cooldown (should skip); atomically updates last_fired otherwise."""
        key: tuple[UUID, str] = (rule_id, symbol)
        async with self._last_fired_lock:
            last = self._last_fired.get(key)
            if last and (now - last).total_seconds() < cooldown_min * _SECONDS_PER_MINUTE:
                return True
            self._last_fired[key] = now
            return False

    async def _mark_triggered(self, rule_id: UUID) -> None:
        try:
            await self._repository.mark_triggered(rule_id)
        except asyncpg.PostgresError as exc:
            db_insert_failures_total.labels(operation="rule_status").inc()
            logger.error(
                "failed_to_mark_rule_triggered",
                rule_id=str(rule_id),
                error=str(exc),
            )
            return

        async with self._rules_lock:
            self._rules_cache = [r for r in self._rules_cache if r.rule_id != rule_id]
