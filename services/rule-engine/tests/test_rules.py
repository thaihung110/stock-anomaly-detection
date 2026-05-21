"""Tests for rules.py — all 6 system rule functions."""
import pytest

from rule_engine.config import Settings
from rule_engine.domain.rules import (
    ALL_RULES,
    rule_bollinger_breakout,
    rule_intraday_range,
    rule_price_zscore,
    rule_rsi_extreme,
    rule_volume_ratio,
    rule_volume_zscore,
)
from rule_engine.domain.schema import AlertSeverity, QuoteEvent, RuleName


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg() -> Settings:
    return Settings(
        price_zscore_trigger=3.0,
        price_zscore_high=4.5,
        vol_zscore_trigger=3.0,
        vol_zscore_high=5.0,
        vol_ratio_trigger=3.5,
        rsi_overbought=80.0,
        rsi_oversold=20.0,
        intraday_range_trigger=0.05,
    )


@pytest.fixture
def quote() -> QuoteEvent:
    return QuoteEvent(
        symbol="AAPL",
        price=150.0,
        change_pct=0.0,
        day_volume=1_000_000,
        day_high=155.0,
        day_low=145.0,
        prev_close=140.0,
        event_ts="2026-05-18T10:00:00Z",
    )


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


# ── rule_price_zscore ──────────────────────────────────────────────────────────


class TestRulePriceZscore:
    def test_no_alert_when_z_below_trigger(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # daily_return = (149.8 - 149.7) / 149.7 ≈ 0.000667; z ≈ 0.067 < 3.0
        q = QuoteEvent(
            symbol="AAPL",
            price=149.8,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=150.0,
            day_low=149.5,
            prev_close=149.7,
            event_ts="2026-05-18T10:00:00Z",
        )
        assert rule_price_zscore(q, ctx, cfg) is None

    def test_medium_alert_when_z_between_trigger_and_high(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # daily_return = (107 - 100) / 100 = 0.07; z = 0.07 / 0.01 = 7.0
        # Use std=0.02 so z = 0.07/0.02 = 3.5 → between 3.0 and 4.5
        local_ctx = dict(ctx)
        local_ctx["std_return_20d"] = 0.02
        q = QuoteEvent(
            symbol="AAPL",
            price=107.0,
            change_pct=7.0,
            day_volume=1_000_000,
            day_high=108.0,
            day_low=104.0,
            prev_close=100.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        alert = rule_price_zscore(q, local_ctx, cfg)
        assert alert is not None
        assert alert.rule_name == RuleName.PRICE_ZSCORE
        assert alert.severity == AlertSeverity.MEDIUM

    def test_high_alert_when_z_above_high_threshold(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # daily_return = 0.05; z = 0.05 / 0.01 = 5.0 > 4.5
        q = QuoteEvent(
            symbol="AAPL",
            price=105.0,
            change_pct=5.0,
            day_volume=1_000_000,
            day_high=106.0,
            day_low=103.0,
            prev_close=100.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        alert = rule_price_zscore(q, ctx, cfg)
        assert alert is not None
        assert alert.severity == AlertSeverity.HIGH

    def test_returns_none_when_std_is_zero(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        assert rule_price_zscore(quote, {"std_return_20d": 0.0}, cfg) is None

    def test_returns_none_when_prev_close_is_zero(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        q = QuoteEvent(
            symbol="AAPL",
            price=100.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=101.0,
            day_low=99.0,
            prev_close=0.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        assert rule_price_zscore(q, ctx, cfg) is None

    def test_alert_symbol_matches_quote(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        q = QuoteEvent(
            symbol="MSFT",
            price=105.0,
            change_pct=5.0,
            day_volume=1_000_000,
            day_high=106.0,
            day_low=103.0,
            prev_close=100.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        alert = rule_price_zscore(q, ctx, cfg)
        assert alert is not None
        assert alert.symbol == "MSFT"


# ── rule_volume_zscore ─────────────────────────────────────────────────────────


class TestRuleVolumeZscore:
    def test_no_alert_when_z_below_trigger(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # z = (600k - 500k) / 100k = 1.0 < 3.0
        q = quote.model_copy(update={"day_volume": 600_000})
        assert rule_volume_zscore(q, ctx, cfg) is None

    def test_medium_alert_when_z_between_trigger_and_high(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # z = (850k - 500k) / 100k = 3.5, between 3.0 and 5.0
        q = quote.model_copy(update={"day_volume": 850_000})
        alert = rule_volume_zscore(q, ctx, cfg)
        assert alert is not None
        assert alert.rule_name == RuleName.VOLUME_ZSCORE
        assert alert.severity == AlertSeverity.MEDIUM

    def test_high_alert_when_z_above_high_threshold(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # z = (1_100_000 - 500_000) / 100_000 = 6.0 > 5.0
        q = quote.model_copy(update={"day_volume": 1_100_000})
        alert = rule_volume_zscore(q, ctx, cfg)
        assert alert is not None
        assert alert.severity == AlertSeverity.HIGH

    def test_returns_none_when_std_is_zero(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        assert rule_volume_zscore(quote, {"std_volume_20d": 0.0}, cfg) is None

    def test_no_alert_when_volume_below_mean(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # z = (100k - 500k) / 100k = -4.0 < 3.0 trigger
        q = quote.model_copy(update={"day_volume": 100_000})
        assert rule_volume_zscore(q, ctx, cfg) is None


# ── rule_volume_ratio ──────────────────────────────────────────────────────────


class TestRuleVolumeRatio:
    def test_no_alert_below_trigger(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # ratio = 1_000_000 / 500_000 = 2.0 < 3.5
        assert rule_volume_ratio(quote, ctx, cfg) is None

    def test_alert_above_trigger(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # ratio = 2_000_000 / 500_000 = 4.0 > 3.5
        q = quote.model_copy(update={"day_volume": 2_000_000})
        alert = rule_volume_ratio(q, ctx, cfg)
        assert alert is not None
        assert alert.rule_name == RuleName.VOLUME_RATIO
        assert alert.severity == AlertSeverity.MEDIUM

    def test_returns_none_when_mean_volume_zero(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        assert rule_volume_ratio(quote, {"mean_volume_20d": 0.0}, cfg) is None

    def test_triggered_value_is_ratio(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        q = quote.model_copy(update={"day_volume": 2_000_000})
        alert = rule_volume_ratio(q, ctx, cfg)
        assert alert is not None
        assert alert.triggered_value == pytest.approx(4.0, abs=0.01)


# ── rule_bollinger_breakout ────────────────────────────────────────────────────


class TestRuleBollingerBreakout:
    def test_no_alert_when_price_inside_bands(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # price=150, lower=140, upper=160 → bb_pos = (150-140)/20 = 0.5
        q = QuoteEvent(
            symbol="AAPL",
            price=150.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=152.0,
            day_low=148.0,
            prev_close=149.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        assert rule_bollinger_breakout(q, ctx, cfg) is None

    def test_alert_when_price_above_upper_band(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # price=165 > bb_upper=160 → bb_pos = (165-140)/20 = 1.25 > 1.0
        q = QuoteEvent(
            symbol="AAPL",
            price=165.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=166.0,
            day_low=163.0,
            prev_close=159.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        alert = rule_bollinger_breakout(q, ctx, cfg)
        assert alert is not None
        assert alert.rule_name == RuleName.BOLLINGER_BREAKOUT
        assert alert.triggered_value == pytest.approx(1.25, abs=0.01)

    def test_alert_when_price_below_lower_band(
        self, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # price=135 < bb_lower=140 → bb_pos = (135-140)/20 = -0.25 < 0.0
        q = QuoteEvent(
            symbol="AAPL",
            price=135.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=137.0,
            day_low=134.0,
            prev_close=141.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        alert = rule_bollinger_breakout(q, ctx, cfg)
        assert alert is not None
        assert alert.triggered_value == pytest.approx(-0.25, abs=0.01)

    def test_returns_none_when_bb_range_zero(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        ctx = {"bb_upper_20d": 150.0, "bb_lower_20d": 150.0}
        assert rule_bollinger_breakout(quote, ctx, cfg) is None


# ── rule_rsi_extreme ───────────────────────────────────────────────────────────


class TestRuleRsiExtreme:
    def test_no_alert_when_rsi_in_normal_range(
        self, quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
    ) -> None:
        # rsi_14=50 in ctx fixture — strictly between 20 and 80
        assert rule_rsi_extreme(quote, ctx, cfg) is None

    def test_alert_when_rsi_overbought(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        alert = rule_rsi_extreme(quote, {"rsi_14": 85.0}, cfg)
        assert alert is not None
        assert alert.rule_name == RuleName.RSI_EXTREME
        assert alert.triggered_value == pytest.approx(85.0, abs=0.01)
        assert alert.threshold == cfg.rsi_overbought

    def test_alert_when_rsi_oversold(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        alert = rule_rsi_extreme(quote, {"rsi_14": 15.0}, cfg)
        assert alert is not None
        assert alert.triggered_value == pytest.approx(15.0, abs=0.01)
        assert alert.threshold == cfg.rsi_oversold

    def test_alert_at_exact_overbought_boundary(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        # rsi == 80.0 → not in open interval (20, 80) → fires
        assert rule_rsi_extreme(quote, {"rsi_14": 80.0}, cfg) is not None

    def test_alert_at_exact_oversold_boundary(
        self, quote: QuoteEvent, cfg: Settings
    ) -> None:
        # rsi == 20.0 → fires
        assert rule_rsi_extreme(quote, {"rsi_14": 20.0}, cfg) is not None


# ── rule_intraday_range ────────────────────────────────────────────────────────


class TestRuleIntradayRange:
    def test_no_alert_when_range_below_trigger(
        self, cfg: Settings
    ) -> None:
        # range = (102 - 100) / 100 = 0.02 < 0.05
        q = QuoteEvent(
            symbol="AAPL",
            price=101.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=102.0,
            day_low=100.0,
            prev_close=100.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        assert rule_intraday_range(q, {}, cfg) is None

    def test_alert_when_range_above_trigger(
        self, cfg: Settings
    ) -> None:
        # range = (108 - 100) / 100 = 0.08 > 0.05
        q = QuoteEvent(
            symbol="AAPL",
            price=104.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=108.0,
            day_low=100.0,
            prev_close=100.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        alert = rule_intraday_range(q, {}, cfg)
        assert alert is not None
        assert alert.rule_name == RuleName.INTRADAY_RANGE
        assert alert.triggered_value == pytest.approx(0.08, abs=0.001)

    def test_returns_none_when_day_low_is_zero(
        self, cfg: Settings
    ) -> None:
        q = QuoteEvent(
            symbol="AAPL",
            price=100.0,
            change_pct=0.0,
            day_volume=1_000_000,
            day_high=110.0,
            day_low=0.0,
            prev_close=100.0,
            event_ts="2026-05-18T10:00:00Z",
        )
        assert rule_intraday_range(q, {}, cfg) is None


# ── ALL_RULES tuple ────────────────────────────────────────────────────────────


class TestAllRules:
    def test_all_rules_has_six_entries(self) -> None:
        assert len(ALL_RULES) == 6

    def test_no_alerts_on_quiet_quote(self, cfg: Settings) -> None:
        q = QuoteEvent(
            symbol="AAPL",
            price=100.0,
            change_pct=0.001,
            day_volume=500_000,
            day_high=100.5,
            day_low=99.5,
            prev_close=99.9,
            event_ts="2026-05-18T10:00:00Z",
        )
        ctx: dict[str, float] = {
            "std_return_20d": 0.01,
            "mean_return_20d": 0.0,
            "mean_volume_20d": 500_000.0,
            "std_volume_20d": 100_000.0,
            "bb_upper_20d": 110.0,
            "bb_lower_20d": 90.0,
            "rsi_14": 50.0,
        }
        for rule_fn in ALL_RULES:
            assert rule_fn(q, ctx, cfg) is None
