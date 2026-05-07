from datetime import datetime, timezone

import structlog

from .schema import QuoteEvent

log = structlog.get_logger()

# Fields that yfinance omits when their value is zero/default (sparse protobuf).
# Required fields (id, price) are intentionally left as hard [] access so a
# KeyError bubbles up and the message is dropped — a quote without a price or
# symbol is useless.
_OPTIONAL_FLOAT_DEFAULTS: dict[str, float] = {
    "change_percent": 0.0,
    "day_high": 0.0,
    "day_low": 0.0,
    "previous_close": 0.0,
}
_OPTIONAL_INT_DEFAULTS: dict[str, int] = {
    "day_volume": 0,
}


def normalize(raw: dict) -> QuoteEvent:
    """Map a decoded yfinance PricingData dict to QuoteEvent.

    yfinance protobuf field → QuoteEvent field:
      id               → symbol        (required — drops message if absent)
      price            → price         (required — drops message if absent)
      change_percent   → change_pct    (optional, default 0.0)
      day_volume       → day_volume    (optional, default 0)
      day_high         → day_high      (optional, default 0.0)
      day_low          → day_low       (optional, default 0.0)
      previous_close   → prev_close    (optional, default 0.0)
      time             → event_ts      (optional, falls back to now())

    yfinance sends sparse protobuf messages — fields with zero/default values
    are omitted entirely. Optional fields use safe defaults so the message is
    forwarded rather than dropped.
    """
    log.debug(
        "normalizer_raw_message",
        symbol=raw.get("id", "<missing>"),
        present_fields=sorted(raw.keys()),
    )

    ts_raw = raw.get("time")
    if ts_raw:
        # yfinance delivers time in milliseconds; fromtimestamp() expects seconds.
        ts_sec = int(ts_raw) / 1000 if int(ts_raw) > 1e10 else int(ts_raw)
        event_ts = datetime.fromtimestamp(ts_sec, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    else:
        event_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    price: float = raw["price"]  # KeyError intentional — no price = useless tick

    # day_high / day_low fall back to current price when the day range has not
    # yet been established (e.g. very first tick of the session).
    day_high = float(raw.get("day_high") or price)
    day_low = float(raw.get("day_low") or price)

    return QuoteEvent(
        symbol=raw["id"],  # KeyError intentional — no symbol = useless tick
        price=price,
        change_pct=float(raw.get("change_percent") or 0.0),
        day_volume=int(raw.get("day_volume") or 0),
        day_high=day_high,
        day_low=day_low,
        prev_close=float(raw.get("previous_close") or 0.0),
        event_ts=event_ts,
    )
