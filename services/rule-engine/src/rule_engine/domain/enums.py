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
