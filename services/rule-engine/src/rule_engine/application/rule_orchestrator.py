"""System rule orchestration — evaluates built-in rules and publishes alerts."""
from typing import Any, Protocol

import structlog

from rule_engine.config import Settings
from rule_engine.domain.rules import ALL_RULES, RuleFn
from rule_engine.domain.schema import AlertEvent, QuoteEvent

logger = structlog.get_logger(__name__)


class EventPublisher(Protocol):
    """Minimal interface for Kafka alert publishing."""

    async def publish(self, message: Any) -> Any: ...


class RuleOrchestrator:
    """Evaluates system rules against quote events and publishes alerts.

    Accepts an injectable rule tuple so alternative rule sets can be tested
    without modifying orchestration logic.
    """

    def __init__(self, cfg: Settings, rules: tuple[RuleFn, ...] = ALL_RULES) -> None:
        self._cfg = cfg
        self._rules = rules

    async def evaluate(
        self, event: QuoteEvent, ctx: dict[str, float], publisher: EventPublisher
    ) -> int:
        """Evaluate all system rules. Returns the number of alerts fired."""
        fired = 0
        for rule_fn in self._rules:
            alert: AlertEvent | None = rule_fn(event, ctx, self._cfg)
            if alert is None:
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
