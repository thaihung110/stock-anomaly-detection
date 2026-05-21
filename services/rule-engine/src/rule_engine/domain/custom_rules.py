"""Pure functions for evaluating user-defined custom alert rules.

No side effects, no global state writes. All logic is deterministic given inputs.
"""
from rule_engine.domain.enums import AlertField, AlertOperator
from rule_engine.domain.schema import QuoteEvent

BATCH_DAILY_FIELDS: frozenset[AlertField] = frozenset({AlertField.RSI_14, AlertField.BB_POSITION})

_CONTEXT_FIELD_KEYS: dict[AlertField, str] = {
    AlertField.PRICE_ZSCORE: "price_zscore",
    AlertField.VOLUME_ZSCORE: "volume_zscore",
    AlertField.VOLUME_RATIO_20D: "vol_ratio_20d",
    AlertField.RSI_14: "rsi_14",
    AlertField.BB_POSITION: "bb_position",
}


def get_field_value(
    event: QuoteEvent,
    field: AlertField,
    context: dict[str, float] | None,
) -> float | None:
    """Extract the current value for a given AlertField from a quote event and context.

    Returns None when the value is unavailable (context-dependent field with no context,
    or context key missing).
    """
    match field:
        case AlertField.PRICE:
            return event.price
        case AlertField.DAILY_RETURN:
            return event.change_pct
        case AlertField.DAY_VOLUME:
            return float(event.day_volume)
        case _:
            if context is None:
                return None
            return context.get(_CONTEXT_FIELD_KEYS[field])


def evaluate_condition(
    current: float,
    operator: AlertOperator,
    threshold: float,
    prev_value: float | None = None,
) -> bool:
    """Evaluate whether a condition is met for the given operator and threshold.

    CROSSES_UP  : prev_value <= threshold < current  (False when prev_value is None)
    CROSSES_DOWN: prev_value >= threshold > current  (False when prev_value is None)
    """
    match operator:
        case AlertOperator.GT:
            return current > threshold
        case AlertOperator.LT:
            return current < threshold
        case AlertOperator.GTE:
            return current >= threshold
        case AlertOperator.LTE:
            return current <= threshold
        case AlertOperator.CROSSES_UP:
            if prev_value is None:
                return False
            return prev_value <= threshold < current
        case AlertOperator.CROSSES_DOWN:
            if prev_value is None:
                return False
            return prev_value >= threshold > current
