# ohlcv-daily-cleaner

Cleans and normalizes `bronze.raw_ohlcv_daily` into the Silver layer: drops unusable rows, backfills `adj_close`, and derives a VWAP proxy consumed by `rule-engine-context-builder`.

## Data Flow

```
gravitino_bronze.raw.raw_ohlcv_daily
    ↓ readBronze (full table read — no incremental filter)
transform (filter invalid rows, derive fields)
    ↓ MERGE INTO (upsert on `symbol` + `trade_date`)
gravitino_silver.normalized.ohlcv_daily
```

Batch job, not streaming.

## Pipeline Steps

`OhlcvCleanerPipeline` (`pipeline/OhlcvCleanerPipeline.scala`):

1. **`readBronze`**: reads the **entire** `raw_ohlcv_daily` table every run — there's no watermark/incremental filter here (unlike `ohlcv-daily-loader` upstream). `spark.sql.iceberg.dynamic-partition-overwrite.enabled=true` is set specifically to make repeated full-table re-runs idempotent per partition.
2. **`transform`**:
   - Drops any row where `open`/`high`/`low`/`close`/`volume` is null or `<= 0` — logged as `dropped = totalBefore - totalAfter`.
   - Sets `is_complete = adj_close.isNotNull` — flags whether the source row actually had an adjusted close, **before** it gets backfilled in the next step.
   - `adj_close` falls back to raw `close` via `coalesce` when the source value is missing (rare for recent data, per the source comment).
   - Derives `vwap_estimate = (open + high + low + close) / 4` — a cheap daily VWAP proxy (not a true volume-weighted average; there's no intraday data at this grain), explicitly noted in the code as feeding `rule-engine-context-builder` downstream.
   - Stamps `data_source = "yfinance"` and `cleaned_at = now()`.
3. **`mergeInto`**: caches the cleaned DataFrame (since it's both counted and merged), then `MERGE INTO ... ON symbol AND trade_date`. On failure, explicitly unpersists the cache before rethrowing (avoids leaking cached partitions on a failed run).

### Target table: `gravitino_silver.normalized.ohlcv_daily`

`symbol`, `trade_date`, `open`, `high`, `low`, `close`, `adj_close`, `volume`, `dividends`, `stock_splits`, `vwap_estimate`, `data_source`, `is_complete`, `cleaned_at`.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable                                                                                                   | Required | Default                                                                                    | Description                                                   |
| ---------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes      | —                                                                                          | Same shape as every app — see `company-info-loader/README.md` |
| `GRAVITINO_OAUTH_SERVER_URI` / `_TOKEN_PATH` / `_SCOPE`                                                    | No       | `http://openhouse-keycloak` / `realms/iceberg/protocol/openid-connect/token` / `gravitino` |                                                               |
| `INPUT_TABLE`                                                                                              | No       | `gravitino_bronze.raw.raw_ohlcv_daily`                                                     |                                                               |
| `OUTPUT_TABLE`                                                                                             | No       | `gravitino_silver.normalized.ohlcv_daily`                                                  |                                                               |

## Catalog / Connection Config

Registers **both** `gravitino_bronze` (read) and `gravitino_silver` (write) — the only app so far that reads from one warehouse and writes to another. See `CatalogConfigurator.scala`.

## Kubernetes Resource Sizing

From `k8s/ohlcv-daily-cleaner-spark-application.yaml`:

- **Driver**: 1 core, 1Gi memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, 1Gi memory + 256Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- `spark.sql.shuffle.partitions=8` — sized for "moderate parallelism, ~500 symbols × 20 years of daily rows" per the YAML's own comment (full-table read, so this scales with total history, not just one day's worth)

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-ohlcv-daily-cleaner.sh v0.4
./scripts/run-ohlcv-daily-cleaner.sh
./scripts/stop-ohlcv-daily-cleaner.sh
```

> ⚠️ `scripts/build-and-push-ohlcv-daily-cleaner.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/ohlcv-daily-cleaner-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Depends on `ohlcv-daily-loader` having populated `bronze.raw_ohlcv_daily` first — see the [top-level README's First-time Startup Order](../README.md#first-time-startup-order).

## Known Issues

Because `readBronze` does a full-table scan every run (no watermark), runtime and executor memory pressure will grow linearly with total accumulated history across all symbols — worth revisiting if the 20-year backfill window in `ohlcv-daily-loader` is ever widened significantly.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
