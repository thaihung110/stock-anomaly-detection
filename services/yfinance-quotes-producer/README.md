# yfinance-quotes-producer

Streams real-time price quotes for the 500-symbol universe from Yahoo Finance's WebSocket feed and publishes them to Kafka topic `raw.stock.quotes` — the primary input to the Rule Engine.

## Data Flow

```
yfinance WebSocket (yf.AsyncWebSocket, PricingData protobuf)
    ↓ subscribe(symbols) + listen(message_handler)
filter to configured symbol set
    ↓ normalize() — map sparse protobuf → QuoteEvent
raw.stock.quotes (Kafka, key = symbol)
```

Long-running streaming process, not a poller — the connection is held open and yfinance pushes ticks as they occur.

## Pipeline Steps

`yf_client.py` → `normalizer.py` → `producer.py`, wired together in `main.py`:

1. **`stream_quotes`** (`yf_client.py`): opens `yf.AsyncWebSocket`, subscribes to the configured symbol list, and listens via a callback (`on_message`) that pushes onto a bounded `asyncio.Queue` (maxsize 1000 — full queue drops the message with a warning rather than blocking the WebSocket callback). The consumer loop pulls from the queue with a **30s timeout**; if no message arrives within that window, the connection is treated as stale and torn down for reconnect. Messages for symbols outside the configured set are filtered out before yielding.
2. **Reconnect policy**: exponential backoff starting at `reconnect_delay_sec` (default 5s), doubling on each failure up to `reconnect_max_delay_sec` (default 60s), with ±10% jitter to avoid thundering-herd reconnects. Backoff resets to the base delay on every successful connect.
3. **`normalize`** (`normalizer.py`): maps yfinance's sparse protobuf dict to `QuoteEvent`. yfinance omits fields that are zero/default, so:
   - `symbol` (`id`) and `price` are **required** — a missing value raises `KeyError`, which `main.py` catches and drops the message (a quote without a price or symbol is useless).
   - `change_pct`, `day_high`, `day_low`, `prev_close`, `day_volume` are optional with safe defaults (`0.0`/`0`) so a sparse message is still forwarded rather than dropped.
   - `day_high`/`day_low` fall back to the current `price` if absent (e.g. the very first tick of a session, before a day range exists).
   - `time` (epoch, sec or ms — auto-detected by magnitude) → `event_ts` as an ISO-8601 UTC string; falls back to `now()` if absent.
4. **`QuotesProducer.publish`** (`producer.py`): sends via `AIOKafkaProducer` with `acks="all"` and `enable_idempotence=True` — no duplicate/lost messages on producer-side retries.

## Kafka Output Contract

Topic: **`raw.stock.quotes`** · Partition key: `symbol.encode("utf-8")` (all quotes for a symbol land on the same partition).

`QuoteEvent` (`schema.py`):

| Field | Type | Notes |
|---|---|---|
| `symbol` | str | Uppercased by validator |
| `price` | float | Validated `> 0` |
| `change_pct` | float | |
| `day_volume` | int | Coerced from any numeric type |
| `day_high` | float | |
| `day_low` | float | |
| `prev_close` | float | |
| `event_ts` | str | ISO-8601 UTC, e.g. `2026-03-26T10:23:15Z` |

> Field names/types must exactly match what the Rule Engine's quote consumer expects (see `services/rule-engine/README.md`).

## Configuration

Env vars read by `Settings` (`config.py`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | No | `localhost:9092` | |
| `KAFKA_TOPIC` | No | `raw.stock.quotes` | |
| `SYMBOLS` | No | 50 hardcoded tickers (AAPL, MSFT, GOOGL, …) | Comma-separated |
| `RECONNECT_DELAY_SEC` | No | `5.0` | Initial backoff |
| `RECONNECT_MAX_DELAY_SEC` | No | `60.0` | Backoff ceiling |
| `KAFKA_COMPRESSION_TYPE` | No | `gzip` | |

No API key required — yfinance's WebSocket feed is unauthenticated.

## Kubernetes Resource Sizing

From `k8s/yfinance-quotes-producer/deployment.yaml`:

- 1 replica, no resource-based scaling (this is a single persistent WebSocket connection, not horizontally scalable — multiple replicas would each subscribe independently and produce duplicate quotes)
- **Requests**: 100m CPU, 256Mi memory · **Limits**: 500m CPU, 512Mi memory
- `initContainer: wait-for-kafka` — blocks pod start until `openhouse-kafka:9092` is reachable (`nc -z`)

## Build & Run

```bash
cd services
./scripts/build_and_push-yfinance-producer.sh v0.2
./scripts/run-yfinance-producer.sh
./scripts/stop-yfinance-producer.sh
```

> ⚠️ `scripts/build_and_push-yfinance-producer.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/yfinance-quotes-producer/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

> Note the script is named `*-yfinance-producer.sh`, not `*-yfinance-quotes-producer.sh` — it does not match the service directory name. See the [top-level README](../README.md#run--stop-scripts) for the full naming map.

No upstream dependency — this is a root producer with no other services to run first (besides Kafka itself).

## Known Issues

- Single-replica-only design: because each replica opens its own independent WebSocket subscription, scaling to 2+ replicas would **duplicate every quote** on the topic rather than share load. If throughput ever requires scaling, subscription needs to be partitioned by symbol range across replicas — not implemented today.
- A 30s silence on the WebSocket triggers a full reconnect even if the market is simply quiet (e.g. pre-market low-volume periods) — this is intentional (stale-connection detection) but means reconnect logs during illiquid symbols/hours are expected, not necessarily a real outage.

## Testing

No automated tests — no `tests/` directory in this service.
