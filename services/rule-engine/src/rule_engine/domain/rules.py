from collections.abc import Callable

import structlog

from rule_engine.config import Settings
from rule_engine.domain.schema import AlertEvent, AlertSeverity, QuoteEvent, RuleName

logger = structlog.get_logger(__name__)

RuleFn = Callable[[QuoteEvent, dict[str, float], Settings], AlertEvent | None]


def rule_price_zscore(
    quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
) -> AlertEvent | None:
    std = ctx.get("std_return_20d", 0.0)
    if std == 0.0 or quote.prev_close == 0.0:
        return None
    daily_return = (quote.price - quote.prev_close) / quote.prev_close
    z = daily_return / std
    abs_z = abs(z)
    if abs_z <= cfg.price_zscore_trigger:
        return None
    severity = AlertSeverity.HIGH if abs_z > cfg.price_zscore_high else AlertSeverity.MEDIUM
    return AlertEvent.build(
        quote=quote,
        rule_name=RuleName.PRICE_ZSCORE,
        severity=severity,
        triggered_value=round(abs_z, 4),
        threshold=cfg.price_zscore_trigger,
        context_snapshot={
            "mean_return_20d": ctx.get("mean_return_20d", 0.0),
            "std_return_20d": std,
            "z_score": round(z, 4),
        },
    )


def rule_volume_zscore(
    quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
) -> AlertEvent | None:
    std = ctx.get("std_volume_20d", 0.0)
    mean = ctx.get("mean_volume_20d", 0.0)
    if std == 0.0:
        return None
    z = (quote.day_volume - mean) / std
    if z <= cfg.vol_zscore_trigger:
        return None
    severity = AlertSeverity.HIGH if z > cfg.vol_zscore_high else AlertSeverity.MEDIUM
    return AlertEvent.build(
        quote=quote,
        rule_name=RuleName.VOLUME_ZSCORE,
        severity=severity,
        triggered_value=round(z, 4),
        threshold=cfg.vol_zscore_trigger,
        context_snapshot={
            "mean_volume_20d": mean,
            "std_volume_20d": std,
            "z_score": round(z, 4),
        },
    )


def rule_volume_ratio(
    quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
) -> AlertEvent | None:
    mean_vol = ctx.get("mean_volume_20d", 0.0)
    if mean_vol == 0.0:
        return None
    ratio = quote.day_volume / mean_vol
    if ratio <= cfg.vol_ratio_trigger:
        return None
    return AlertEvent.build(
        quote=quote,
        rule_name=RuleName.VOLUME_RATIO,
        severity=AlertSeverity.MEDIUM,
        triggered_value=round(ratio, 4),
        threshold=cfg.vol_ratio_trigger,
        context_snapshot={"mean_volume_20d": mean_vol, "volume_ratio": round(ratio, 4)},
    )


def rule_bollinger_breakout(
    quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
) -> AlertEvent | None:
    bb_upper = ctx.get("bb_upper_20d", 0.0)
    bb_lower = ctx.get("bb_lower_20d", 0.0)
    bb_range = bb_upper - bb_lower
    if bb_range == 0.0:
        return None
    bb_pos = (quote.price - bb_lower) / bb_range
    if 0.0 <= bb_pos <= 1.0:
        return None
    return AlertEvent.build(
        quote=quote,
        rule_name=RuleName.BOLLINGER_BREAKOUT,
        severity=AlertSeverity.MEDIUM,
        triggered_value=round(bb_pos, 4),
        threshold=1.0 if bb_pos > 1.0 else 0.0,
        context_snapshot={
            "bb_upper_20d": bb_upper,
            "bb_lower_20d": bb_lower,
            "bb_position": round(bb_pos, 4),
        },
    )


def rule_rsi_extreme(
    quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
) -> AlertEvent | None:
    rsi = ctx.get("rsi_14", 50.0)
    if cfg.rsi_oversold < rsi < cfg.rsi_overbought:
        return None
    threshold = cfg.rsi_overbought if rsi >= cfg.rsi_overbought else cfg.rsi_oversold
    return AlertEvent.build(
        quote=quote,
        rule_name=RuleName.RSI_EXTREME,
        severity=AlertSeverity.MEDIUM,
        triggered_value=round(rsi, 2),
        threshold=threshold,
        context_snapshot={"rsi_14": rsi},
    )


def rule_intraday_range(
    quote: QuoteEvent, ctx: dict[str, float], cfg: Settings
) -> AlertEvent | None:
    if quote.day_low == 0.0:
        return None
    range_pct = (quote.day_high - quote.day_low) / quote.day_low
    if range_pct <= cfg.intraday_range_trigger:
        return None
    return AlertEvent.build(
        quote=quote,
        rule_name=RuleName.INTRADAY_RANGE,
        severity=AlertSeverity.MEDIUM,
        triggered_value=round(range_pct, 4),
        threshold=cfg.intraday_range_trigger,
        context_snapshot={
            "day_high": quote.day_high,
            "day_low": quote.day_low,
            "range_pct": round(range_pct, 4),
        },
    )


ALL_RULES: tuple[RuleFn, ...] = (
    rule_price_zscore,
    rule_volume_zscore,
    rule_volume_ratio,
    rule_bollinger_breakout,
    rule_rsi_extreme,
    rule_intraday_range,
)
