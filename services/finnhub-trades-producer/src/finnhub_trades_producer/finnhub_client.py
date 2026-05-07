"""
Finnhub WebSocket client with exponential-backoff reconnect.

Yields lists of raw trade dicts from Finnhub's trade messages.
Each raw dict contains Finnhub's native field names (s, p, v, t, c);
normalizer.py is responsible for mapping them to TradeTick.
"""

import asyncio
import json
import random
from collections.abc import AsyncIterator
from typing import Any

import structlog
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from finnhub_trades_producer.config import Settings
from finnhub_trades_producer.metrics import WS_RECONNECTS

logger = structlog.get_logger(__name__)


async def _subscribe_all(ws: Any, symbols: list[str]) -> None:
    for symbol in symbols:
        msg = json.dumps({"type": "subscribe", "symbol": symbol})
        await ws.send(msg)
    logger.info("subscribed_to_symbols", count=len(symbols))


async def stream_trades(config: Settings) -> AsyncIterator[list[dict]]:
    """
    Async generator that yields raw Finnhub trade tick lists.

    Reconnects indefinitely on any WebSocket or network failure using
    exponential backoff with ±10 % jitter, capped at reconnect_max_delay_sec.
    """
    delay = config.reconnect_delay_sec

    while True:
        try:
            logger.info("ws_connecting", url="wss://ws.finnhub.io")
            async with websockets.connect(
                config.finnhub_ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                await _subscribe_all(ws, config.symbols_list)
                delay = config.reconnect_delay_sec  # reset backoff on successful connect

                async for raw_msg in ws:
                    try:
                        data = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        logger.warning("ws_invalid_json", raw=raw_msg[:120])
                        continue

                    msg_type = data.get("type")

                    if msg_type == "trade":
                        ticks: list[dict] = data.get("data", [])
                        if ticks:
                            logger.debug(
                                "ws_trade_batch_received",
                                tick_count=len(ticks),
                                symbols=list({t.get("s") for t in ticks}),
                            )
                            yield ticks
                    elif msg_type == "ping":
                        logger.debug("ws_ping_received")
                    elif msg_type == "error":
                        logger.error("ws_finnhub_error", detail=data.get("msg"))
                    else:
                        logger.debug("ws_unknown_message_type", msg_type=msg_type)

        except (ConnectionClosed, WebSocketException, OSError, asyncio.TimeoutError) as exc:
            WS_RECONNECTS.inc()
            jitter = delay * random.uniform(-0.1, 0.1)  # noqa: S311
            wait = delay + jitter
            logger.warning("ws_disconnected", error=str(exc), retry_in_sec=round(wait, 1))
            await asyncio.sleep(wait)
            delay = min(delay * 2, config.reconnect_max_delay_sec)
