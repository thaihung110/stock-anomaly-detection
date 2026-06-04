"""System rule orchestration — evaluates built-in rules and publishes alerts."""
import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from rule_engine.config import Settings
from rule_engine.domain.rules import ALL_RULES, RuleFn
from rule_engine.domain.schema import AlertEvent, QuoteEvent

logger = structlog.get_logger(__name__)

_SECONDS_PER_MINUTE = 60


class EventPublisher(Protocol):
    """Minimal interface for Kafka alert publishing."""

    async def publish(self, message: Any) -> Any: ...


class RuleOrchestrator:
    """Evaluates system rules against quote events and publishes alerts.

    Tracks per-(symbol, rule) cooldowns in memory to prevent the same condition
    from generating duplicate alerts when it remains true across many quotes
    (e.g. RSI from daily batch stays > 80 all day).

    Accepts an injectable rule tuple so alternative rule sets can be tested
    without modifying orchestration logic.
    """

    def __init__(self, cfg: Settings, rules: tuple[RuleFn, ...] = ALL_RULES) -> None:
        self._cfg = cfg
        self._rules = rules
        self._last_fired: dict[tuple[str, str], datetime] = {}
        self._last_fired_lock = asyncio.Lock()

    async def evaluate(
        self, event: QuoteEvent, ctx: dict[str, float], publisher: EventPublisher
    ) -> int:
        """Evaluate all system rules. Returns the number of alerts fired."""
        fired = 0
        now = datetime.now(UTC)
        for rule_fn in self._rules:
            alert: AlertEvent | None = rule_fn(event, ctx, self._cfg)
            if alert is None:
                continue
            if not await self._claim_fire_slot(alert.symbol, alert.rule_name.value, now):
                logger.debug(
                    "alert_suppressed_cooldown",
                    symbol=alert.symbol,
                    rule=alert.rule_name.value,
                )
                continue
            await publisher.publish(alert)
            logger.info(
                "alert_fired",
                symbol=alert.symbol,
                rule=alert.rule_name.value,
                severity=alert.severity.value,
                value=alert.triggered_value,
            )
            fired += 1
        return fired

    async def _claim_fire_slot(self, symbol: str, rule_name: str, now: datetime) -> bool:
        """Atomically check the cooldown and, if clear, record this fire.

        Returns True when the alert is allowed to fire (and the cooldown is now
        armed); False when the (symbol, rule) pair is still within its cooldown
        window. Check-and-record happen under a single lock so two concurrently
        processed quotes cannot both pass the check before either records.
        """
        key = (symbol, rule_name)
        window_s = self._cfg.system_alert_cooldown_min * _SECONDS_PER_MINUTE
        async with self._last_fired_lock:
            last = self._last_fired.get(key)
            if last is not None and (now - last).total_seconds() < window_s:
                return False
            self._last_fired[key] = now
            return True
