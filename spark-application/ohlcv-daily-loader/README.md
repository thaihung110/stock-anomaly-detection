# ohlcv-daily-loader

Incrementally loads daily OHLCV bars from Yahoo Finance for a fixed symbol list, using a per-symbol watermark to fetch only new trading days.

## Data Flow

```
Yahoo Finance (via YahooFinanceClient)
    ↓ per-symbol incremental fetch (watermark → today)
OhlcvRow (batched every FETCH_BATCH_SIZE symbols)
    ↓ MERGE INTO (upsert on `symbol` + `trade_date`)
gravitino_bronze.raw.raw_ohlcv_daily
```

Batch job, run manually or on a schedule (daily, per `CLAUDE.md`'s 06:00 UTC slot) — not streaming.

## Pipeline Steps

`OhlcvPipeline` (`pipeline/OhlcvPipeline.scala`):

1. **Watermark read**: `getWatermarks` runs `SELECT symbol, MAX(trade_date) FROM <table> GROUP BY symbol` to find the last loaded date per symbol. On the very first run (table not yet populated), this fails and falls back to an empty map — logged as a warning, not an error.
2. For each symbol: fetch range is `watermark + 1 day → today` if a watermark exists, otherwise `today - BACKFILL_YEARS years → today` (full historical backfill for a brand-new symbol).
3. If the fetch range starts after today, the symbol is already up to date and is skipped entirely (no API call).
4. Fetched rows accumulate in an in-memory buffer, which is flushed (merged into Iceberg) every `FETCH_BATCH_SIZE` symbols — not after every single symbol — to cap driver heap usage during a large backfill. Comment in the source: a 20-year backfill is ~5k rows/symbol; at `fetchBatchSize=10` that's ~50k rows (~20MB) per flush, well within the 512Mi–1Gi driver heap.
5. `mergeRows` runs `MERGE INTO ... ON symbol AND trade_date` — upsert, safe to re-run.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app — see `company-info-loader/README.md` |
| `GRAVITINO_OAUTH_SERVER_URI` / `_TOKEN_PATH` / `_SCOPE` | No | `http://openhouse-keycloak` / `realms/iceberg/protocol/openid-connect/token` / `gravitino` | |
| `SYMBOLS_FILE` | No | `/opt/spark/conf/symbols.txt` (overridden to `/tmp/symbols.txt` in `k8s/`, mounted from the `ohlcv-loader-symbols` ConfigMap) | |
| `OUTPUT_TABLE` | No | `gravitino_bronze.raw.raw_ohlcv_daily` | |
| `FETCH_BATCH_SIZE` | No | `50` (set to `10` in `k8s/`) | Symbols processed between each Iceberg merge flush |
| `BACKFILL_YEARS` | No | `20` | How far back to fetch for a symbol with no existing watermark |

### Target table: `gravitino_bronze.raw.raw_ohlcv_daily`

| Column | Notes |
|---|---|
| `symbol`, `trade_date` | Merge key |
| `open`, `high`, `low`, `close`, `adj_close` | `Option[Double]` — nullable if Yahoo returns a gap |
| `volume` | `Option[Long]` |
| `dividends`, `stock_splits` | Corporate action fields Yahoo returns per-bar (`Double`, `0.0` when none) |
| `source` | Data source tag |
| `ingested_at` | Load timestamp |

## Catalog / Connection Config

Same `gravitino_bronze` REST catalog pattern as `company-info-loader` — see that app's README or `CatalogConfigurator.scala` here for the exact keys.

## Kubernetes Resource Sizing

From `k8s/ohlcv-daily-loader-spark-application.yaml`:

- **Driver**: 1 core, 1Gi memory + 512Mi overhead
- **Executor**: 1 instance, 1 core, 1Gi memory + 512Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- Larger heap than `company-info-loader` because a 20-year backfill across 50 symbols is a meaningfully bigger working set (comment in the YAML: `spark.memory.fraction=0.6` reserved for the `MERGE INTO` shuffle specifically to avoid driver OOM)
- S3A tuned down from chart defaults (`readahead.range=8MB`, `connection.maximum=20`) to save executor heap during Iceberg file scans

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-ohlcv-daily-loader.sh v0.5
./scripts/run-ohlcv-daily-loader.sh
./scripts/stop-ohlcv-daily-loader.sh
```

> ⚠️ `scripts/build-and-push-ohlcv-daily-loader.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/ohlcv-daily-loader-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires the `ohlcv-loader-symbols` ConfigMap and `spark-app-secrets` Secret — see the [top-level README](../README.md#prerequisites-required-before-running-any-spark-app).

## Known Issues

None specific to this app. Note that `rule-engine-context-builder` (downstream) depends on this job having run for the current trading day — see that app's README for the weekend/early-morning date mismatch issue this can cause further down the pipeline.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
