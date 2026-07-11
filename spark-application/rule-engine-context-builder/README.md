# rule-engine-context-builder

Builds `gold.rule_engine_context` — a single-day snapshot of rolling stats and technical indicator baselines that the (Python) Rule Engine service loads into memory at startup to evaluate anomaly rules without recomputing 20-day windows itself.

## Data Flow

```
gravitino_gold.gold.fact_ohlcv_daily (60-day lookback window)  ─┐
gravitino_gold.gold.dim_symbol (active)                        ─┴─→ join → 5-day rolling stats
    ↓ filter to exactly one date_key (as_of_date)
    ↓ overwritePartitions() — dynamic partition overwrite
gravitino_gold.gold.rule_engine_context
```

Batch job, not streaming. Scheduled at 07:00 UTC per `CLAUDE.md`, right after `fact-ohlcv-daily-builder`; the Rule Engine service reloads at 07:15 UTC.

## Pipeline Steps

`RuleEngineContextPipeline` (`pipeline/RuleEngineContextPipeline.scala`):

1. **Resolve `as_of_date_key`**: `AS_OF_DATE_KEY` env var if set, otherwise `defaultAsOfDateKey()` = **UTC yesterday** as `YYYYMMDD`. See Known Issues below — this default is the source of a recurring failure mode.
2. **Guard**: reads `dim_symbol` (active only) first and aborts immediately with a clear error if it's empty ("Run `company_info_loader → dim_loader` first") — fails fast instead of silently producing an empty context.
3. **`readFact`**: reads `fact_ohlcv_daily` filtered to `date_key >= as_of_date_key - 60 calendar days`. The 60-day lookback (not 20) is deliberately generous — the code comment notes it "safely covers 20+ trading days even with holidays" (weekends/holidays mean 20 trading days can span more than 20 calendar days).
4. **`withSymbolAndRolling`**: joins the 60-day fact slice against `dim_symbol` (broadcast), computes a **5-day** rolling window (`mean_return_5d`, `std_return_5d`, `mean_volume_5d`, `vwap_5d_avg` — narrower than `fact-ohlcv-daily-builder`'s 20-day stats, which are just carried through), then filters down to exactly `date_key = as_of_date_key` — a single trading day's worth of rows, one per active symbol.
5. **`overwritePartition`**: if the filtered result is **zero rows**, throws immediately with a message telling the operator to check that `fact_ohlcv_daily` has data for the target `date_key` — this is the exact failure described in Known Issues below. Otherwise writes via `overwritePartitions()` (DataFrameWriterV2, same mechanism as `news-cleaner`).

### Target table: `gravitino_gold.gold.rule_engine_context`

```sql
symbol VARCHAR(20), as_of_date DATE,  -- PK
mean_return_20d, std_return_20d, mean_return_5d, std_return_5d DOUBLE,
mean_volume_20d, std_volume_20d, mean_volume_5d DOUBLE,
bb_upper_20d, bb_lower_20d, bb_mid_20d, atr_14, rsi_14, vwap_5d_avg DOUBLE,
updated_at TIMESTAMP
```

Note the 20-day fields (`mean_return_20d`, `bb_upper_20d`, `atr_14`, `rsi_14`, etc.) are **carried through from `fact_ohlcv_daily` as-is** — only the 5-day stats and `vwap_5d_avg` are computed freshly in this job.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app |
| `GRAVITINO_OAUTH_SERVER_URI` / `_TOKEN_PATH` / `_SCOPE` | No | `http://openhouse-keycloak` / `realms/iceberg/protocol/openid-connect/token` / `gravitino` | |
| `INPUT_TABLE` | No | `gravitino_gold.gold.fact_ohlcv_daily` | |
| `DIM_SYMBOL_TABLE` | No | `gravitino_gold.gold.dim_symbol` | |
| `OUTPUT_TABLE` | No | `gravitino_gold.gold.rule_engine_context` | |
| `AS_OF_DATE_KEY` | No | UTC yesterday (`YYYYMMDD`) | Manual override for backfill/weekend runs — see Known Issues |

## Catalog / Connection Config

Registers `gravitino_gold` only — both input and output live in the Gold warehouse.

## Kubernetes Resource Sizing

From `k8s/rule-engine-context-builder-spark-application.yaml`:

- **Driver**: 1 core, 1Gi memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, 1Gi memory + 256Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- `spark.sql.shuffle.partitions=4` — small working set (60-day slice, filtered to 1 day)

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-rule-engine-context-builder.sh v0.5
./scripts/run-rule-engine-context-builder.sh
./scripts/stop-rule-engine-context-builder.sh
```

> ⚠️ `scripts/build-and-push-rule-engine-context-builder.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/rule-engine-context-builder-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Depends on `fact-ohlcv-daily-builder` having run for the target trading day, and `dim-loader` for `dim_symbol`.

## Known Issues

### Fails with "0 rows produced" on weekends or early-morning runs

**Symptom**:
```
RuntimeException: overwritePartitions to gravitino_catalog.gold.rule_engine_context aborted:
0 rows produced. Check that fact_ohlcv_daily has data for the target date_key and dim_symbol is populated.
```

**Root cause**: `defaultAsOfDateKey()` defaults to **UTC yesterday**. Yahoo Finance only returns OHLCV for trading days (Mon–Fri, excluding US market holidays). If this job runs on a **Saturday, Sunday, or before NYSE open (~14:30 UTC)** on a weekday, the most recent trading day actually present in `fact_ohlcv_daily` is *older* than "yesterday" — the date filter matches zero rows.

Confirmed occurrence (2026-05-24, Saturday): `ohlcv-daily-loader` had last fetched data ending `trade_date = 2026-05-22` (Friday); `defaultAsOfDateKey()` computed `20260523` (also a Friday, but one week later — i.e. yesterday relative to the Saturday run) with no matching rows in the fact table.

**Fix**: override `AS_OF_DATE_KEY` to the last known trading day before running manually:
```yaml
- name: AS_OF_DATE_KEY
  value: "20260522"
```

⚠️ **`k8s/rule-engine-context-builder-spark-application.yaml` currently still has `AS_OF_DATE_KEY: "20260522"` hardcoded from this past incident.** Per the original fix note, this should be removed (or updated) once the pipeline resumes normal weekday runs — leaving it in place pins every run to that single historical date instead of the automatic UTC-yesterday default.

**When this does NOT happen**: a normal weekday run (triggered after NYSE close, ~21:00 UTC+) will have `ohlcv-daily-loader` already fetched that day's data, so the automatic default resolves correctly.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
