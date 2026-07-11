# fact-ohlcv-daily-builder

Joins Silver OHLCV against the `dim_symbol`/`dim_date` dimensions and computes the full set of rolling statistics and technical indicators for `gold.fact_ohlcv_daily` — the central daily fact table.

## Data Flow

```
gravitino_silver.normalized.ohlcv_daily  ─┐
gravitino_gold.gold.dim_symbol (active)  ─┼─→ join → per-symbol window functions (returns, z-scores,
gravitino_gold.gold.dim_date             ─┘         Bollinger, RSI, ATR, MACD)
    ↓ MERGE INTO (upsert on symbol_key + date_key)
gravitino_gold.gold.fact_ohlcv_daily
```

Batch job, not streaming. This is the most computationally involved app in the pipeline — every technical indicator downstream (`rule-engine-context-builder`, dashboards) is derived here.

## Pipeline Steps

`FactOhlcvPipeline` (`pipeline/FactOhlcvPipeline.scala`), run as 4 stages:

**Stage 1 — Read inputs**: silver OHLCV, `dim_symbol` filtered to `is_active = true` only (so a renamed/delisted symbol's old SCD2 row doesn't produce duplicate joins), `dim_date` (just `full_date`/`date_key`).

**Stage 2 — `joinDims`**: broadcast-joins silver against both dimensions (`dim_symbol` and `dim_date` are small — broadcast avoids a shuffle on the large fact side).

**Stage 3 — `computeIndicators`**, all computed via `Window.partitionBy("symbol_key").orderBy("date_key")` (ordering by the **integer** `date_key`, not a `Date` column — exact sort, no timezone/parsing ambiguity):

- **Returns**: `daily_return`, `log_return`, `gap_pct` (vs. previous close), `intraday_range_pct`, `dollar_volume`. All null-guarded against a zero/null previous close.
- **20-day rolling stats**: `mean_return_20d`, `std_return_20d`, `mean_volume_20d`, `std_volume_20d` — `rowsBetween(-19, 0)`.
- **Z-scores**: `price_zscore = daily_return / std_return_20d`, `volume_zscore = (volume - mean_volume_20d) / std_volume_20d`.
- **Bollinger Bands** (20d, 2σ): `bb_mid`/`bb_upper`/`bb_lower` from rolling mean/stddev of `adj_close`; `bb_position` normalized to `[0,1]` between the bands.
- **RSI-14**: SMA-based (not Wilder's original EMA-based smoothing) — the code comment explicitly notes this is "a close approximation at daily granularity, consistent with `rule-engine-context-builder`" (i.e. deliberately kept consistent with the other RSI implementation in this pipeline, not a bug).
- **ATR-14**: 14-period SMA of True Range (`max(high-low, |high-prevClose|, |low-prevClose|)`).
- **MACD (12/26/9 EMA)**: **the one indicator that can't be expressed as a Spark window function** — EMA has a true sequential recurrence (each value depends on the previous EMA value, not a fixed lookback window). Implemented via `collect_list` to gather each symbol's ordered close-price array, a Scala UDF (`macdUdf`/`emaArray`) that computes the EMA-12, EMA-26, MACD line, signal-9, and histogram arrays in-JVM, then `posexplode` + `element_at` to zip the results back onto the original rows by row-number. Symbols with fewer than 26 closes get `NULL` MACD values (not enough history for EMA-26).

**Stage 4 — `mergeInto`**: caches, then `MERGE INTO ... ON symbol_key AND date_key` — same cache/unpersist-on-failure pattern as the Silver cleaners.

### Target table: `gravitino_gold.gold.fact_ohlcv_daily`

```sql
symbol_key INT, date_key INT,  -- composite PK, FK to dim_symbol/dim_date
open, high, low, close, adj_close, vwap DOUBLE, volume BIGINT, dollar_volume DOUBLE,
daily_return, log_return, intraday_range_pct, gap_pct DOUBLE,
mean_return_20d, std_return_20d, mean_volume_20d, std_volume_20d DOUBLE,
price_zscore, volume_zscore DOUBLE,
rsi_14, macd_line, macd_signal, macd_histogram DOUBLE,
bb_upper, bb_lower, bb_mid, bb_position, atr_14 DOUBLE,
data_source VARCHAR(20), loaded_at TIMESTAMP
```

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app |
| `GRAVITINO_OAUTH_SERVER_URI` / `_TOKEN_PATH` / `_SCOPE` | No | `http://openhouse-keycloak` / `realms/iceberg/protocol/openid-connect/token` / `gravitino` | Correct defaults (unlike `dim-loader`) |
| `INPUT_TABLE` | No | `gravitino_silver.normalized.ohlcv_daily` | |
| `DIM_SYMBOL_TABLE` | No | `gravitino_gold.gold.dim_symbol` | |
| `DIM_DATE_TABLE` | No | `gravitino_gold.gold.dim_date` | |
| `OUTPUT_TABLE` | No | `gravitino_gold.gold.fact_ohlcv_daily` | |

## Catalog / Connection Config

Registers all three warehouses it touches: reads `gravitino_silver` and `gravitino_gold` (for the dims), writes `gravitino_gold` (the fact table).

## Kubernetes Resource Sizing

From `k8s/fact-ohlcv-daily-builder-spark-application.yaml`:

- **Driver**: 1 core, **1Gi** memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, **3Gi** memory + 256Mi overhead — the largest executor heap of any batch app in this project, sized for the `collect_list`/UDF-based MACD computation which materializes a full close-price array per symbol in memory
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- `spark.sql.shuffle.partitions=16` — highest of the batch jobs, "reduced to match executor cores" per the YAML's own comment
- AWS credential env vars are mapped correctly here (`AWS_ACCESS_KEY_ID` ← `MINIO_ACCESS_KEY`) — contrast with the bug in `dim-loader`'s YAML

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-fact-ohlcv-daily-builder.sh v0.5
./scripts/run-fact-ohlcv-daily-builder.sh
./scripts/stop-fact-ohlcv-daily-builder.sh
```

> ⚠️ `scripts/build-and-push-fact-ohlcv-daily-builder.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/fact-ohlcv-daily-builder-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Depends on `ohlcv-daily-cleaner` (for `silver.ohlcv_daily`) and `dim-loader` (for `dim_symbol`/`dim_date`) having already run.

## Known Issues

None specific to this app. The MACD collect-per-symbol approach means memory usage scales with the **longest** per-symbol history in the input (up to 20 years × ~5k rows for a symbol with no gaps) — this is exactly why the executor is sized at 3Gi rather than the 512Mi–1Gi typical of the simpler apps.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
