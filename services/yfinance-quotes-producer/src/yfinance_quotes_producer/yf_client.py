import asyncio
import random
from collections.abc import AsyncGenerator
from typing import Any

import structlog
import yfinance as yf

from .config import Settings
from .metrics import YF_WS_RECONNECTS

log = structlog.get_logger()


async def stream_quotes(settings: Settings) -> AsyncGenerator[dict[str, Any], None]:
    """Async generator yielding decoded yfinance PricingData dicts.

    Reconnects with exponential backoff + ±10% jitter on any failure.
    Only yields messages whose symbol is in the configured symbols set.
    """
    symbols = settings.symbols_list
    symbol_set = set(symbols)
    delay = settings.reconnect_delay_sec

    while True:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

        def on_message(msg: dict[str, Any]) -> None:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("yfinance_ws_queue_full_drop", symbol=msg.get("id", "<unknown>"))

        ws = yf.AsyncWebSocket(verbose=False)
        listen_task: asyncio.Task | None = None  # guard against UnboundLocalError in finally
        try:
            await ws.subscribe(symbols)
            listen_task = asyncio.create_task(ws.listen(message_handler=on_message))

            log.info("yfinance_ws_connected", symbols_count=len(symbols))
            delay = settings.reconnect_delay_sec  # reset on successful connect

            while True:
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # No message for 30 s — treat as stale connection and reconnect.
                    log.warning("yfinance_ws_timeout_no_message")
                    break

                symbol = raw.get("id", "")
                if symbol not in symbol_set:
                    log.debug("yfinance_ws_symbol_filtered", symbol=symbol)
                    continue
                yield raw

        except Exception as exc:
            log.warning("yfinance_ws_error", error=str(exc), error_type=type(exc).__name__)
        finally:
            if listen_task is not None:
                listen_task.cancel()
                await asyncio.gather(listen_task, return_exceptions=True)
                log.debug("yfinance_ws_listen_task_cancelled")

        YF_WS_RECONNECTS.inc()
        jitter = delay * 0.1 * (2 * random.random() - 1)
        sleep_for = min(delay + jitter, settings.reconnect_max_delay_sec)
        log.info("yfinance_ws_reconnecting", sleep_sec=round(sleep_for, 2))
        await asyncio.sleep(sleep_for)
        delay = min(delay * 2, settings.reconnect_max_delay_sec)
