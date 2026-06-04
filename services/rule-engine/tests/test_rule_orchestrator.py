"""Tests for rule_orchestrator.py — RuleOrchestrator."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rule_engine.config import Settings
from rule_engine.application.rule_orchestrator import RuleOrchestrator
from rule_engine.domain.rules import ALL_RULES
from rule_engine.domain.schema import AlertEvent, AlertSeverity, QuoteEvent, RuleName


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_quote(symbol: str = "AAPL", price: float = 150.0) -> QuoteEvent:
    return QuoteEvent(
        symbol=symbol,
        price=price,
        change_pct=5.0,
        day_volume=1_000_000,
        day_high=155.0,
        day_low=145.0,
        prev_close=100.0,
        event_ts="2026-05-18T10:00:00Z",
    )


def _make_alert(quote: QuoteEvent | None = None) -> AlertEvent:
    q = quote or _make_quote()
    return AlertEvent.build(
        quote=q,
        rule_name=RuleName.PRICE_ZSCORE,
        severity=AlertSeverity.MEDIUM,
        triggered_value=3.5,
        threshold=3.0,
        context_snapshot={},
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg() -> Settings:
    return Settings()


@pytest.fixture
def ctx() -> dict[str, float]:
    return {
        "mean_return_20d": 0.001,
        "std_return_20d": 0.01,
        "mean_volume_20d": 500_000.0,
        "std_volume_20d": 100_000.0,
        "bb_upper_20d": 160.0,
        "bb_lower_20d": 140.0,
        "rsi_14": 50.0,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRuleOrchestrator:
    @pytest.mark.asyncio
    async def test_evaluate_returns_zero_when_no_rule_fires(
        self, cfg: Settings, ctx: dict[str, float]
    ) -> None:
        rule_fn = MagicMock(return_value=None)
        orchestrator = RuleOrchestrator(cfg, rules=(rule_fn,))
        publisher = AsyncMock()

        result = await orchestrator.evaluate(_make_quote(), ctx, publisher)

        assert result == 0
        publisher.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluate_publishes_alert_when_rule_fires(
        self, cfg: Settings, ctx: dict[str, float]
    ) -> None:
        quote = _make_quote()
        alert = _make_alert(quote)
        rule_fn = MagicMock(return_value=alert)
        orchestrator = RuleOrchestrator(cfg, rules=(rule_fn,))
        publisher = AsyncMock()

        result = await orchestrator.evaluate(quote, ctx, publisher)

        assert result == 1
        publisher.publish.assert_awaited_once_with(alert)

    @pytest.mark.asyncio
    async def test_evaluate_counts_multiple_alerts(
        self, cfg: Settings, ctx: dict[str, float]
    ) -> None:
        # Two rules with different rule_names so they don't share a cooldown key.
        alert_a = _make_alert()  # PRICE_ZSCORE
        alert_b = AlertEvent.build(
            quote=_make_quote(),
            rule_name=RuleName.VOLUME_ZSCORE,
            severity=AlertSeverity.MEDIUM,
            triggered_value=4.0,
            threshold=3.0,
            context_snapshot={},
        )
        rule_a = MagicMock(return_value=alert_a)
        rule_b = MagicMock(return_value=alert_b)
        rule_c = MagicMock(return_value=None)
        orchestrator = RuleOrchestrator(cfg, rules=(rule_a, rule_b, rule_c))
        publisher = AsyncMock()

        result = await orchestrator.evaluate(_make_quote(), ctx, publisher)

        assert result == 2
        assert publisher.publish.await_count == 2

    @pytest.mark.asyncio
    async def test_evaluate_passes_event_ctx_cfg_to_rule(
        self, cfg: Settings, ctx: dict[str, float]
    ) -> None:
        quote = _make_quote()
        rule_fn = MagicMock(return_value=None)
        orchestrator = RuleOrchestrator(cfg, rules=(rule_fn,))

        await orchestrator.evaluate(quote, ctx, AsyncMock())

        rule_fn.assert_called_once_with(quote, ctx, cfg)

    def test_default_rules_are_all_rules(self, cfg: Settings) -> None:
        orchestrator = RuleOrchestrator(cfg)
        assert orchestrator._rules is ALL_RULES

    @pytest.mark.asyncio
    async def test_evaluate_skips_publish_when_no_alert(
        self, cfg: Settings, ctx: dict[str, float]
    ) -> None:
        orchestrator = RuleOrchestrator(cfg, rules=(MagicMock(return_value=None),))
        publisher = AsyncMock()

        await orchestrator.evaluate(_make_quote(), ctx, publisher)

        publisher.publish.assert_not_awaited()
