# finnhub-trades-producer

Streams individual trade ticks from Finnhub's WebSocket feed and publishes them to Kafka topic `raw.stock.trades` — the tick-level source `spark-application/trades-ohlcv-stream` aggregates into 1-minute OHLCV bars.

## Data Flow

```
Finnhub WebSocket (wss://ws.finnhub.io, subscribe per symbol)
    ↓ batch of raw trade dicts per "trade" message
normalize() — map Finnhub fields → TradeTick
    ↓
raw.stock.trades (Kafka, key = symbol)
```

Long-running streaming process — the connection is held open and Finnhub pushes trades as they execute.

## Pipeline Steps

`finnhub_client.py` → `normalizer.py` → `producer.py`, wired in `main.py`:

1. **`stream_trades`** (`finnhub_client.py`): connects to `wss://ws.finnhub.io?token=<api_key>`, sends one `{"type": "subscribe", "symbol": ...}` message per configured symbol, then reads messages in a loop. Message types handled: `trade` (yields the tick batch), `ping` (logged at debug, no-op), `error` (logged), anything else (logged at debug and ignored).
2. **Reconnect policy**: on `ConnectionClosed`, `WebSocketException`, `OSError`, or a timeout, reconnects with exponential backoff (`reconnect_delay_sec` → `reconnect_max_delay_sec`, ±10% jitter), same shape as `yfinance-quotes-producer`. Backoff resets to base delay on every successful reconnect. `ping_interval=20s` / `ping_timeout=10s` keep the connection alive and detect silent drops.
3. **`normalize`** (`normalizer.py`): maps Finnhub's short field names (`s`→`symbol`, `p`→`price`, `v`→`volume`, `t`→`timestamp_ms`, `c`→`conditions`) to `TradeTick`. Raises `pydantic.ValidationError` on invalid values (e.g. non-positive price) — caught per-tick in `main.py._run`, which drops just that tick and continues (does not crash the whole batch).
4. **`TradesProducer.publish`** (`producer.py`): `AIOKafkaProducer`, `acks="all"`, `enable_idempotence=True`, keyed by symbol — **this partitioning is a hard requirement**, not just an optimization: `trades-ohlcv-stream`'s windowed aggregation depends on all of one symbol's ticks landing on the same partition to produce correct 1-minute bars.

## Kafka Output Contract

Topic: **`raw.stock.trades`** · Partition key: `symbol.encode("utf-8")`.

`TradeTick` (`schema.py`) — source-of-truth contract mirrored by the Scala `StructType` in `spark-application/trades-ohlcv-stream/.../schema/TradeSchema.scala`:

| Field | Type | Notes |
|---|---|---|
| `symbol` | str | Uppercased, non-empty |
| `price` | float | Validated `> 0` |
| `volume` | int | **Coerced from float** — Finnhub may send `150.0`; must serialize as integer since Spark reads this as `LongType` |
| `timestamp_ms` | int | Epoch **milliseconds** — validator rejects values that look like seconds (Spark derives `bar_ts = (timestamp_ms / 1000L).cast(TimestampType)`; a seconds-epoch value here would silently produce wrong OHLCV windows) |
| `conditions` | list[str] \| null | Empty list normalized to `null` |

## Configuration

Env vars read by `Settings` (`config.py`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `FINNHUB_API_KEY` | **Yes** | — | Also used to build `finnhub_ws_url` |
| `KAFKA_BOOTSTRAP_SERVERS` | No | `localhost:9092` | |
| `KAFKA_TOPIC` | No | `raw.stock.trades` | |
| `KAFKA_COMPRESSION_TYPE` | No | `gzip` | |
| `SYMBOLS` | No | 50 hardcoded tickers | Comma-separated |
| `RECONNECT_DELAY_SEC` | No | `5.0` | Initial backoff |
| `RECONNECT_MAX_DELAY_SEC` | No | `60.0` | Backoff ceiling |

## Kubernetes Resource Sizing

From `k8s/finnhub-trades-producer/deployment.yaml`:

- 1 replica · **Requests**: 100m CPU, 128Mi memory · **Limits**: 250m CPU, 256Mi memory
- `initContainer: wait-for-kafka` — blocks pod start until `openhouse-kafka:9092` is reachable
- `FINNHUB_API_KEY` sourced from Secret `finnhub-trades-producer-secrets` — **this is the Secret both Finnhub producers depend on**; create it once and both `finnhub-trades-producer` and `finnhub-news-producer` reference it (see `k8s/finnhub-trades-producer/finnhub_secret.yaml` for the template)

## Build & Run

```bash
cd services
./scripts/build_and_push-finnhub-producer.sh v0.4
./scripts/run-finnhub-producer.sh
./scripts/stop-finnhub-producer.sh
```

> ⚠️ `scripts/build_and_push-finnhub-producer.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/finnhub-trades-producer/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

> Note the script is named `*-finnhub-producer.sh`, not `*-finnhub-trades-producer.sh` — it does not match the service directory name. See the [top-level README](../README.md#run--stop-scripts) for the full naming map.

No upstream service dependency besides Kafka. You must create the `finnhub-trades-producer-secrets` Secret (see `k8s/finnhub-trades-producer/finnhub_secret.yaml`) before either Finnhub producer will start successfully.

## Known Issues

- Same single-replica constraint as `yfinance-quotes-producer`: each replica independently subscribes to every configured symbol over its own WebSocket connection, so running 2+ replicas would duplicate every trade tick rather than share load.
- No per-tick backpressure handling — if Kafka is slower to accept sends than Finnhub is to push ticks, `await self._producer.send(...)` will simply queue/block inside `aiokafka`'s internal buffer; there is no explicit bounded queue or drop policy on the producer side (contrast with `yfinance-quotes-producer`'s explicit `asyncio.Queue(maxsize=1000)` on the consumer side).

## Testing

Has a `.pytest_cache/` directory but no `tests/` source directory — no automated tests currently exist for this service.
