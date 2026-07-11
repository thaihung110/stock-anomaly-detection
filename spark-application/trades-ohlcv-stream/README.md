# trades-ohlcv-stream

Structured Streaming job: consumes raw tick trades from Kafka (published by `finnhub-trades-producer`) and aggregates them into 1-minute OHLCV bars directly into the Silver layer.

## Data Flow

```
Kafka topic `raw.stock.trades`  (finnhub-trades-producer)
    ↓ Structured Streaming, micro-batch every 60s
parse JSON → watermark(5 min) → tumbling 1-min window groupBy(symbol, window)
    ↓ aggregate (open/high/low/close/volume/vwap/trade_count)
    ↓ append
gravitino_silver.normalized.ohlcv_1min
```

**Streaming, long-running** — no `timeToLiveSeconds`, expected to run continuously. Unlike `news-ingest-stream` (pure append pass-through), this job does real windowed aggregation.

## Pipeline Steps

`TradesOhlcvPipeline` (`pipeline/TradesOhlcvPipeline.scala`):

1. **`buildRawStream`**: subscribes to Kafka topic `raw.stock.trades`, `startingOffsets=earliest`, `maxOffsetsPerTrigger=20000` (much higher than `news-ingest-stream`'s 5000 — tick-level trade volume is far higher than news article volume), `failOnDataLoss=false`.
2. **`transform`**:
   - Parses the Kafka `value` against `TradeSchema`: `symbol`, `price`, `volume`, `timestamp_ms`, `conditions` (array, currently unused downstream).
   - `timestamp_ms` (epoch **milliseconds**, per the project's Kafka schema contract) → `bar_ts` via `(timestamp_ms / 1000L).cast(TimestampType)`.
   - `withWatermark("bar_ts", "5 minutes")`, then `groupBy(symbol, window(bar_ts, "1 minute"))` — a tumbling (non-overlapping) 1-minute window per symbol.
   - Aggregates per window: `open` = price at the earliest `timestamp_ms` in the window (`min_by`), `close` = price at the latest (`max_by`), `high`/`low` = `max`/`min` price, `volume` = `sum(volume)`, `trade_count` = row count, `vwap` = volume-weighted average price (`Σ(price×volume) / Σ(volume)`).
   - Output columns: `bar_ts` (window start), `symbol`, `open`, `high`, `low`, `close`, `volume`, `trade_count`, `vwap`, `bar_date` (derived from window start).
3. **`write`**: appends to Iceberg, `Trigger.ProcessingTime(TRIGGER_INTERVAL)` (default 60s).

Same `BatchProgressListener` pattern as `news-ingest-stream` — logs batch ID, input row count, watermark, and Kafka offsets per micro-batch.

### Target table: `gravitino_silver.normalized.ohlcv_1min`

`bar_ts`, `symbol`, `open`, `high`, `low`, `close`, `volume`, `trade_count`, `vwap`, `bar_date`.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app — see `company-info-loader/README.md` |
| `KAFKA_BOOTSTRAP_SERVERS` | Yes | — | |
| `CHECKPOINT_LOCATION` | Yes | — | e.g. `s3a://checkpoints/trades-ohlcv-stream` — must be unique per streaming app |
| `OUTPUT_TABLE` | No | `gravitino_silver.normalized.ohlcv_1min` | |
| `INPUT_TOPIC` | No | `raw.stock.trades` | |
| `TRIGGER_INTERVAL` | No | `60 seconds` | |
| `KAFKA_MAX_OFFSETS_PER_TRIGGER` | No | `20000` | |
| `ICEBERG_WRITE_DISTRIBUTION_MODE` / `ICEBERG_TARGET_FILE_SIZE_BYTES` / `ICEBERG_FANOUT_ENABLED` | No | `hash` / `134217728` / `false` | |

## Catalog / Connection Config

Registers `gravitino_silver` (not `gravitino_bronze`) against the `silver` warehouse — same OAuth2/Keycloak + MinIO pattern as every other app, see `CatalogConfigurator.scala`.

## Kubernetes Resource Sizing

From `k8s/trades-ohlcv-stream-spark-application.yaml`:

- **Driver**: 1 core, 512Mi memory + 384Mi overhead
- **Executor**: 1 instance, 1 core, 768Mi memory + 384Mi overhead
- **`restartPolicy`**: `OnFailure`, 10 retries, 10s interval; no TTL (long-running)
- Same streaming-reliability tuning as `news-ingest-stream` (RocksDB state store, `compactOnCommit`, `stopGracefullyOnShutdown`, `minBatchesToRetain=5`) — the RocksDB state store here backs the windowed `groupBy` aggregation state, not just a dedup set

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-trades-ohlcv-stream.sh v1.3
./scripts/run-trades-ohlcv.sh
./scripts/stop-trades-ohlcv.sh
```

> Script names are `run-trades-ohlcv.sh` / `stop-trades-ohlcv.sh`, not `run-trades-ohlcv-stream.sh`.

> ⚠️ `scripts/build-and-push-trades-ohlcv-stream.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/trades-ohlcv-stream-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires `spark-app-secrets` and topic `raw.stock.trades` already created.

## Known Issues

None specific to this app. Because this job does true aggregation (not pass-through append), a **watermark of 5 minutes** means trades arriving more than 5 minutes late relative to the current max event time are silently dropped from their window — acceptable for this use case (near-real-time bars) but worth knowing if historical backfill via this topic is ever attempted.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
