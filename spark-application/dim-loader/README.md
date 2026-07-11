# dim-loader

Loads/refreshes every dimension table in the Gold star schema: 3 static lookup tables, 2 pre-generated calendar tables, and 1 SCD Type 2 table for symbol metadata.

## Data Flow

```
Static seed data (hardcoded INSERTs)          ─┐
Generated date sequence (2000-01-01→2040-12-31)├─→ gravitino_gold.gold.dim_anomaly_type
Generated time sequence (0000-2359)            │   gravitino_gold.gold.dim_rule
gravitino_bronze.raw.raw_company_info         ─┘   gravitino_gold.gold.dim_news_category
    ↓ SCD2 change detection                        gravitino_gold.gold.dim_time
                                                     gravitino_gold.gold.dim_date
                                                     gravitino_gold.gold.dim_symbol (SCD2)
```

Batch job, not streaming. Run once to seed, then re-run periodically (weekly, per `CLAUDE.md`) to refresh `dim_symbol`.

## Pipeline Steps

`DimLoaderApp` runs 4 independent sub-pipelines in sequence, each idempotent in its own way:

1. **`DimStaticPipeline.seedIfEmpty`** — `dim_anomaly_type` (6 rows: `PRICE_SPIKE`, `PRICE_DROP`, `VOLUME_SURGE`, `BOLLINGER_BREAKOUT`, `RSI_EXTREME`, `INTRADAY_RANGE`), `dim_rule` (6 rows, one per rule engine rule: `PRICE_Z`, `VOLUME_Z`, `VOLUME_RATIO`, `BOLLINGER`, `RSI`, `INTRADAY`, with their default thresholds), `dim_news_category` (3 rows: `NEWS_EXPLAINED`, `UNEXPLAINED`, `UNCERTAIN`). Each table is checked for emptiness (`SELECT COUNT(*)`) before seeding — a no-op on every run after the first.
2. **`DimTimePipeline.populateIfEmpty`** — generates all 1440 minutes of a day (`time_key = HHMM`), classifying each into `market_session` (`PRE` 04:00–09:29, `REGULAR` 09:30–15:59, `POST` 16:00–19:59, `CLOSED` otherwise) and flagging `is_opening_hour`/`is_closing_hour`. Same emptiness-check idempotency.
3. **`DimDatePipeline.populateIfEmpty`** — generates every calendar date from 2000-01-01 to 2040-12-31 (~15k rows), with a hand-rolled NYSE holiday calendar (New Year's, MLK Day, Presidents Day, Good Friday via the Anonymous Gregorian Easter algorithm, Memorial Day, Juneteenth since 2022, July 4th, Labor Day, Thanksgiving, Christmas — all with weekend-observed-date shifting). `is_trading_day = !is_weekend && !is_us_market_holiday`, and `trading_day_number` is a running count of trading days within each year.
4. **`DimSymbolPipeline.run`** — the only pipeline that isn't a one-time seed; this is a real **SCD Type 2** refresh:
   - Reads the latest row per symbol from `raw_company_info` (by `fetched_at DESC`), renamed to `dim_symbol` column names.
   - Left-joins against currently-active (`is_active = true`) `dim_symbol` rows.
   - Detects changes using **null-safe inequality** (`<=>`, not `=!=`) across all SCD-tracked fields (`company_name`, `exchange`, `sector`, `industry`, `country`, `currency`, `market_cap`, `shares_outstanding`, `beta`, `week_52_high`, `week_52_low`) — this matters because plain `=!=` would miss a `NULL → value` transition.
   - For changed symbols: `MERGE INTO` closes the old record (`is_active = false`, `effective_to = yesterday`).
   - For both changed and brand-new symbols: computes new `symbol_key` values by taking `MAX(symbol_key) + row_number()`, then appends new active rows (`effective_from = today`, `effective_to = NULL`).

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app |
| `GRAVITINO_OAUTH_SERVER_URI` | No | ⚠️ `http://openhouse-keycloak:8080` in code, but **always overridden** to `http://openhouse-keycloak` (no port) in `k8s/` | |
| `GRAVITINO_OAUTH_TOKEN_PATH` | No | ⚠️ `realms/master/protocol/openid-connect/token` in code, but **always overridden** to `realms/iceberg/...` in `k8s/` | |
| `GRAVITINO_OAUTH_SCOPE` | No | ⚠️ `openid` in code, but **always overridden** to `gravitino` in `k8s/` | |
| `INPUT_TABLE` | No | `gravitino_bronze.raw.raw_company_info` | Source for `dim_symbol` |
| `OUTPUT_NAMESPACE` | No | `gravitino_gold` (set to `gravitino_gold.gold` in `k8s/`) | |

### Gold dimension table schemas

```sql
-- dim_symbol (SCD2)
symbol_key INTEGER PK, symbol VARCHAR(20), company_name, exchange, sector, industry,
country, currency, market_cap BIGINT, shares_outstanding BIGINT, beta, week_52_high, week_52_low,
is_active BOOLEAN, effective_from DATE, effective_to DATE, source VARCHAR(20)

-- dim_date
date_key INTEGER PK (YYYYMMDD), full_date, day_of_week, day_name, day_of_month, day_of_year,
week_of_year, month_number, month_name, quarter, year, is_weekend, is_us_market_holiday,
is_trading_day, trading_day_number

-- dim_time
time_key INTEGER PK (HHMM), hour, minute, time_label, market_session, session_minute,
is_opening_hour, is_closing_hour

-- dim_anomaly_type / dim_rule / dim_news_category — small static lookup tables, see seed data above
```

## Catalog / Connection Config

Registers `gravitino_gold` (the only reader of `gravitino_bronze` for this app is `DimSymbolPipeline`). Same OAuth2/Keycloak + MinIO pattern as every other app.

## Kubernetes Resource Sizing

From `k8s/dim-loader-spark-application.yaml`:

- **Driver**: 1 core, 512Mi memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, 512Mi memory + 256Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- Low `spark.sql.shuffle.partitions=2` — "small data, all dims < 15k rows" per the YAML's own comment

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-dim-loader.sh v0.8
./scripts/run-dim-loader.sh
./scripts/stop-dim-loader.sh
```

> ⚠️ `scripts/build-and-push-dim-loader.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/dim-loader-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Depends on `company-info-loader` having populated `bronze.raw_company_info` for `dim_symbol` to have source data (the 3 static/generated dims have no upstream dependency).

## Known Issues

⚠️ **Wrong AWS credential mapping in `k8s/dim-loader-spark-application.yaml`**: both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are sourced from the `MINIO_SECRET_KEY` key of `spark-app-secrets` (should be `MINIO_ACCESS_KEY` for `AWS_ACCESS_KEY_ID`, matching every other app's YAML, e.g. `company-info-loader-spark-application.yaml`). This means any code path using the plain `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars (rather than the Gravitino-catalog-scoped S3 credentials set explicitly by `CatalogConfigurator`) would authenticate with the wrong access key. Worth fixing to match the other apps' YAML.

The `AppConfig.scala` fallback defaults for `GRAVITINO_OAUTH_SERVER_URI`/`_TOKEN_PATH`/`_SCOPE` also don't match the values used everywhere else in this project (port `8080`, realm `master`, scope `openid` vs. no port, realm `iceberg`, scope `gravitino`) — harmless today because `k8s/dim-loader-spark-application.yaml` always sets the correct values explicitly, but a footgun if this jar is ever run outside that YAML (e.g. locally) without setting those env vars.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
