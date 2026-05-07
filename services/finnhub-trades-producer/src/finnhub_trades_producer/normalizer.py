"""
Maps a raw Finnhub trade tick dict → TradeTick.

Finnhub trade tick field mapping
  Finnhub key  │  Meaning                  │  TradeTick field
  ─────────────┼───────────────────────────┼─────────────────
  s            │  symbol                   │  symbol
  p            │  last price               │  price (float)
  v            │  volume                   │  volume (int; Finnhub sends float)
  t            │  timestamp (epoch ms)     │  timestamp_ms (int)
  c            │  conditions (list|None)   │  conditions

Reference: https://finnhub.io/docs/api/websocket-trades
"""

from finnhub_trades_producer.schema import TradeTick


def normalize(raw_tick: dict) -> TradeTick:
    """
    Convert one Finnhub raw tick dict to a validated TradeTick.

    Raises pydantic.ValidationError if required fields are missing or invalid.
    Callers should catch this and discard the tick rather than crashing.
    """
    return TradeTick(
        symbol=raw_tick["s"],
        price=raw_tick["p"],
        volume=raw_tick["v"],        # validator coerces float → int
        timestamp_ms=raw_tick["t"],  # already epoch ms
        conditions=raw_tick.get("c") or None,  # empty list → None
    )
