"""Tests for custom_rules.py — TDD RED phase.

Covers: get_field_value() for all AlertField variants,
evaluate_condition() for all AlertOperator variants including
CROSSES_UP/CROSSES_DOWN edge cases (None prev_value, boundary values).
"""
import pytest

from rule_engine.domain.custom_rules import BATCH_DAILY_FIELDS, evaluate_condition, get_field_value
from rule_engine.domain.enums import AlertField, AlertOperator
from rule_engine.domain.schema import QuoteEvent


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def quote() -> QuoteEvent:
    return QuoteEvent(
        symbol="AAPL",
        price=150.0,
        change_pct=2.5,
        day_volume=1_000_000,
        day_high=152.0,
        day_low=148.0,
        prev_close=146.5,
        event_ts="2026-05-18T10:00:00Z",
    )


@pytest.fixture
def context() -> dict[str, float]:
    # Raw batch context values as loaded by context_loader (no pre-computed
    # price_zscore/volume_zscore/etc. — those are computed on-the-fly).
    return {
        "mean_return_20d": 0.0,
        "std_return_20d": 0.01,
        "mean_volume_20d": 500_000.0,
        "std_volume_20d": 200_000.0,
        "bb_upper_20d": 155.0,
        "bb_lower_20d": 145.0,
        "rsi_14": 75.0,
    }


# ── get_field_value ───────────────────────────────────────────────────────────


class TestGetFieldValue:
    def test_price(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        assert get_field_value(quote, AlertField.PRICE, context) == 150.0

    def test_daily_return(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        assert get_field_value(quote, AlertField.DAILY_RETURN, context) == 2.5

    def test_day_volume_returns_float(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        result = get_field_value(quote, AlertField.DAY_VOLUME, context)
        assert result == 1_000_000.0
        assert isinstance(result, float)

    def test_price_zscore(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        # daily_return = (150 - 146.5) / 146.5; z = (daily_return - 0) / 0.01
        expected = ((quote.price - quote.prev_close) / quote.prev_close) / context["std_return_20d"]
        result = get_field_value(quote, AlertField.PRICE_ZSCORE, context)
        assert result == pytest.approx(expected)

    def test_volume_zscore(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        # (1_000_000 - 500_000) / 200_000 = 2.5
        expected = (float(quote.day_volume) - context["mean_volume_20d"]) / context["std_volume_20d"]
        result = get_field_value(quote, AlertField.VOLUME_ZSCORE, context)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(2.5)

    def test_volume_ratio_20d(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        # 1_000_000 / 500_000 = 2.0
        expected = float(quote.day_volume) / context["mean_volume_20d"]
        result = get_field_value(quote, AlertField.VOLUME_RATIO_20D, context)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(2.0)

    def test_rsi_14(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        assert get_field_value(quote, AlertField.RSI_14, context) == 75.0

    def test_bb_position(self, quote: QuoteEvent, context: dict[str, float]) -> None:
        # (150 - 145) / (155 - 145) = 5 / 10 = 0.5
        expected = (quote.price - context["bb_lower_20d"]) / (
            context["bb_upper_20d"] - context["bb_lower_20d"]
        )
        result = get_field_value(quote, AlertField.BB_POSITION, context)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(0.5)

    # ── Zero-denominator guards ──

    def test_price_zscore_returns_none_when_std_zero(self, quote: QuoteEvent) -> None:
        ctx = {"mean_return_20d": 0.0, "std_return_20d": 0.0}
        assert get_field_value(quote, AlertField.PRICE_ZSCORE, ctx) is None

    def test_price_zscore_returns_none_when_prev_close_zero(self, context: dict[str, float]) -> None:
        q = QuoteEvent(
            symbol="AAPL", price=150.0, change_pct=0.0, day_volume=1_000_000,
            day_high=152.0, day_low=148.0, prev_close=0.0, event_ts="2026-05-18T10:00:00Z",
        )
        assert get_field_value(q, AlertField.PRICE_ZSCORE, context) is None

    def test_volume_zscore_returns_none_when_std_zero(self, quote: QuoteEvent) -> None:
        ctx = {"mean_volume_20d": 500_000.0, "std_volume_20d": 0.0}
        assert get_field_value(quote, AlertField.VOLUME_ZSCORE, ctx) is None

    def test_volume_ratio_returns_none_when_mean_zero(self, quote: QuoteEvent) -> None:
        ctx = {"mean_volume_20d": 0.0}
        assert get_field_value(quote, AlertField.VOLUME_RATIO_20D, ctx) is None

    def test_bb_position_returns_none_when_range_zero(self, quote: QuoteEvent) -> None:
        ctx = {"bb_upper_20d": 150.0, "bb_lower_20d": 150.0}
        assert get_field_value(quote, AlertField.BB_POSITION, ctx) is None

    # ── None/empty context ──

    @pytest.mark.parametrize(
        "field",
        [
            AlertField.PRICE_ZSCORE,
            AlertField.VOLUME_ZSCORE,
            AlertField.VOLUME_RATIO_20D,
            AlertField.RSI_14,
            AlertField.BB_POSITION,
        ],
    )
    def test_context_dependent_fields_return_none_when_no_context(
        self, quote: QuoteEvent, field: AlertField
    ) -> None:
        assert get_field_value(quote, field, None) is None

    @pytest.mark.parametrize(
        "field",
        [
            AlertField.PRICE_ZSCORE,
            AlertField.VOLUME_ZSCORE,
            AlertField.VOLUME_RATIO_20D,
            AlertField.RSI_14,
            AlertField.BB_POSITION,
        ],
    )
    def test_context_dependent_fields_return_none_when_key_missing(
        self, quote: QuoteEvent, field: AlertField
    ) -> None:
        # Empty dict → zero denominators for computed fields, missing key for RSI_14.
        assert get_field_value(quote, field, {}) is None

    def test_price_ignores_none_context(self, quote: QuoteEvent) -> None:
        assert get_field_value(quote, AlertField.PRICE, None) == 150.0

    def test_daily_return_ignores_none_context(self, quote: QuoteEvent) -> None:
        assert get_field_value(quote, AlertField.DAILY_RETURN, None) == 2.5

    def test_day_volume_ignores_none_context(self, quote: QuoteEvent) -> None:
        assert get_field_value(quote, AlertField.DAY_VOLUME, None) == 1_000_000.0


# ── evaluate_condition ────────────────────────────────────────────────────────


class TestEvaluateConditionGT:
    @pytest.mark.parametrize("current,threshold,expected", [
        (5.0, 3.0, True),
        (3.0, 3.0, False),
        (2.0, 3.0, False),
    ])
    def test_gt(self, current: float, threshold: float, expected: bool) -> None:
        assert evaluate_condition(current, AlertOperator.GT, threshold) == expected


class TestEvaluateConditionLT:
    @pytest.mark.parametrize("current,threshold,expected", [
        (2.0, 3.0, True),
        (3.0, 3.0, False),
        (4.0, 3.0, False),
    ])
    def test_lt(self, current: float, threshold: float, expected: bool) -> None:
        assert evaluate_condition(current, AlertOperator.LT, threshold) == expected


class TestEvaluateConditionGTE:
    @pytest.mark.parametrize("current,threshold,expected", [
        (5.0, 3.0, True),
        (3.0, 3.0, True),
        (2.0, 3.0, False),
    ])
    def test_gte(self, current: float, threshold: float, expected: bool) -> None:
        assert evaluate_condition(current, AlertOperator.GTE, threshold) == expected


class TestEvaluateConditionLTE:
    @pytest.mark.parametrize("current,threshold,expected", [
        (2.0, 3.0, True),
        (3.0, 3.0, True),
        (4.0, 3.0, False),
    ])
    def test_lte(self, current: float, threshold: float, expected: bool) -> None:
        assert evaluate_condition(current, AlertOperator.LTE, threshold) == expected


class TestEvaluateConditionCrossesUp:
    """CROSSES_UP: prev_value <= threshold < current."""

    @pytest.mark.parametrize("current,threshold,prev,expected", [
        (4.0, 3.0, 2.5, True),   # prev < threshold, current > threshold
        (4.0, 3.0, 3.0, True),   # prev == threshold (boundary), current > threshold
        (4.0, 3.0, 3.5, False),  # prev already above threshold — no cross
        (2.0, 3.0, 1.5, False),  # current still below threshold
        (3.0, 3.0, 2.0, False),  # current == threshold — not strictly above
    ])
    def test_crosses_up(
        self, current: float, threshold: float, prev: float, expected: bool
    ) -> None:
        assert evaluate_condition(current, AlertOperator.CROSSES_UP, threshold, prev) == expected

    def test_crosses_up_returns_false_when_prev_is_none(self) -> None:
        assert evaluate_condition(5.0, AlertOperator.CROSSES_UP, 3.0, None) is False

    def test_crosses_up_default_prev_none_returns_false(self) -> None:
        assert evaluate_condition(5.0, AlertOperator.CROSSES_UP, 3.0) is False


class TestEvaluateConditionCrossesDown:
    """CROSSES_DOWN: prev_value >= threshold > current."""

    @pytest.mark.parametrize("current,threshold,prev,expected", [
        (2.0, 3.0, 4.0, True),   # prev > threshold, current < threshold
        (2.0, 3.0, 3.0, True),   # prev == threshold (boundary), current < threshold
        (2.0, 3.0, 2.5, False),  # prev already below threshold — no cross
        (4.0, 3.0, 5.0, False),  # current still above threshold
        (3.0, 3.0, 4.0, False),  # current == threshold — not strictly below
    ])
    def test_crosses_down(
        self, current: float, threshold: float, prev: float, expected: bool
    ) -> None:
        assert evaluate_condition(current, AlertOperator.CROSSES_DOWN, threshold, prev) == expected

    def test_crosses_down_returns_false_when_prev_is_none(self) -> None:
        assert evaluate_condition(1.0, AlertOperator.CROSSES_DOWN, 3.0, None) is False

    def test_crosses_down_default_prev_none_returns_false(self) -> None:
        assert evaluate_condition(1.0, AlertOperator.CROSSES_DOWN, 3.0) is False


# ── BATCH_DAILY_FIELDS constant ───────────────────────────────────────────────


class TestBatchDailyFields:
    def test_rsi_14_in_batch_daily(self) -> None:
        assert AlertField.RSI_14 in BATCH_DAILY_FIELDS

    def test_bb_position_in_batch_daily(self) -> None:
        assert AlertField.BB_POSITION in BATCH_DAILY_FIELDS

    def test_price_not_in_batch_daily(self) -> None:
        assert AlertField.PRICE not in BATCH_DAILY_FIELDS

    def test_volume_zscore_not_in_batch_daily(self) -> None:
        assert AlertField.VOLUME_ZSCORE not in BATCH_DAILY_FIELDS
