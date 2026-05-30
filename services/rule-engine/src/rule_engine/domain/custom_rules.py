"""Pure functions for evaluating user-defined custom alert rules.

No side effects, no global state writes. All logic is deterministic given inputs.
"""
from rule_engine.domain.enums import AlertField, AlertOperator
from rule_engine.domain.schema import QuoteEvent

BATCH_DAILY_FIELDS: frozenset[AlertField] = frozenset({AlertField.RSI_14, AlertField.BB_POSITION})

# Remaining context-only fields (looked up directly from the batch context dict).
# Computed fields (PRICE_ZSCORE, VOLUME_ZSCORE, VOLUME_RATIO_20D, BB_POSITION) are
# derived on-the-fly in get_field_value — they are NOT keys in the context dict.
_CONTEXT_FIELD_KEYS: dict[AlertField, str] = {
    AlertField.RSI_14: "rsi_14",
}


def get_field_value(
    event: QuoteEvent,
    field: AlertField,
    context: dict[str, float] | None,
) -> float | None:
    """Return the current value for an AlertField given a quote event and batch context.

    Computed fields (price_zscore, volume_zscore, volume_ratio_20d, bb_position) are
    derived from the event + context on every call so that custom rules on these fields
    work identically to the corresponding system rules.

    Returns None when the value cannot be computed (missing context, zero denominator).
    """
    match field:
        case AlertField.PRICE:
            return event.price
        case AlertField.DAILY_RETURN:
            return event.change_pct
        case AlertField.DAY_VOLUME:
            return float(event.day_volume)
        case AlertField.PRICE_ZSCORE:
            if context is None or event.prev_close == 0.0:
                return None
            std = context.get("std_return_20d", 0.0)
            if std == 0.0:
                return None
            daily_return = (event.price - event.prev_close) / event.prev_close
            return (daily_return - context.get("mean_return_20d", 0.0)) / std
        case AlertField.VOLUME_ZSCORE:
            if context is None:
                return None
            std = context.get("std_volume_20d", 0.0)
            if std == 0.0:
                return None
            return (float(event.day_volume) - context.get("mean_volume_20d", 0.0)) / std
        case AlertField.VOLUME_RATIO_20D:
            if context is None:
                return None
            mean_vol = context.get("mean_volume_20d", 0.0)
            if mean_vol == 0.0:
                return None
            return float(event.day_volume) / mean_vol
        case AlertField.BB_POSITION:
            if context is None:
                return None
            bb_upper = context.get("bb_upper_20d", 0.0)
            bb_lower = context.get("bb_lower_20d", 0.0)
            bb_range = bb_upper - bb_lower
            if bb_range == 0.0:
                return None
            return (event.price - bb_lower) / bb_range
        case _:
            # RSI_14 and any future pure context-lookup fields
            if context is None:
                return None
            key = _CONTEXT_FIELD_KEYS.get(field)
            return context.get(key) if key is not None else None


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
