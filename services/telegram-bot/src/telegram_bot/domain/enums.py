from enum import Enum


class AlertField(str, Enum):
    PRICE = "price"
    DAILY_RETURN = "daily_return"
    DAY_VOLUME = "day_volume"
    VOLUME_ZSCORE = "volume_zscore"
    VOLUME_RATIO_20D = "volume_ratio_20d"
    PRICE_ZSCORE = "price_zscore"
    RSI_14 = "rsi_14"            # batch daily — reflects end-of-previous-day value
    BB_POSITION = "bb_position"  # batch daily — reflects end-of-previous-day value


class AlertOperator(str, Enum):
    GT = ">"
    LT = "<"
    GTE = ">="
    LTE = "<="
    CROSSES_UP = "CROSSES_UP"
    CROSSES_DOWN = "CROSSES_DOWN"


class AlertStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    TRIGGERED = "TRIGGERED"


class AlertFrequency(str, Enum):
    ONCE = "ONCE"
    EVERY_TIME = "EVERY_TIME"


# Fields sourced from batch (end-of-previous-day context, not real-time intraday).
BATCH_DAILY_FIELDS: frozenset[AlertField] = frozenset({AlertField.RSI_14, AlertField.BB_POSITION})

# Human-readable display names for field values.
FIELD_DISPLAY: dict[AlertField, str] = {
    AlertField.PRICE: "price",
    AlertField.DAILY_RETURN: "daily_return",
    AlertField.DAY_VOLUME: "day_volume",
    AlertField.VOLUME_ZSCORE: "volume_zscore",
    AlertField.VOLUME_RATIO_20D: "volume_ratio_20d",
    AlertField.PRICE_ZSCORE: "price_zscore",
    AlertField.RSI_14: "rsi_14",
    AlertField.BB_POSITION: "bb_position",
}

# Maps user-typed operator tokens to AlertOperator enum values.
OPERATOR_TOKENS: dict[str, AlertOperator] = {
    ">": AlertOperator.GT,
    "<": AlertOperator.LT,
    ">=": AlertOperator.GTE,
    "<=": AlertOperator.LTE,
    "crosses_up": AlertOperator.CROSSES_UP,
    "crosses_down": AlertOperator.CROSSES_DOWN,
}
