# company-info-loader

Fetches company profile and fundamental metrics from the Finnhub API for a fixed symbol list, and upserts them into the Bronze layer.

## Data Flow

```
Finnhub REST API (/stock/profile2, /stock/metric?metric=all)
    ↓ FinnhubCompanyClient (per-symbol HTTP calls)
CompanyInfoRow (one row per symbol)
    ↓ MERGE INTO (upsert on `symbol`)
gravitino_bronze.raw.raw_company_info
```

Batch job, run manually or on a schedule via Airflow — not streaming. Every run re-fetches all configured symbols and upserts (`MERGE INTO ... WHEN MATCHED THEN UPDATE ... WHEN NOT MATCHED THEN INSERT`), so it's safe to re-run.

## Pipeline Steps

`CompanyInfoPipeline.run` (`pipeline/CompanyInfoPipeline.scala`):

1. For each symbol in the input list, call `FinnhubCompanyClient.fetchInfo(symbol, apiKey)`. A symbol that returns no data is logged and skipped (not an error — the job continues).
2. Collect all successfully-fetched rows into a `List[CompanyInfoRow]`.
3. If the list is non-empty, register it as a temp view and run a single `MERGE INTO` against `gravitino_bronze.raw.raw_company_info`, keyed on `symbol`.
4. If Finnhub returned nothing for every symbol, log a warning and skip the merge entirely (no destructive write).

Table creation (`CatalogConfigurator.ensureTableExists`) is idempotent — `CREATE NAMESPACE IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`, safe to run every startup.

### Target table: `gravitino_bronze.raw.raw_company_info`

Unpartitioned (only ~50 rows — one per symbol, partitioning would add overhead for no benefit). Several columns are always `NULL` because they aren't available on Finnhub's free tier:

| Column                                                                | Source                                                       | Notes                                                            |
| --------------------------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------- |
| `symbol`                                                              | —                                                            | Primary merge key                                                |
| `short_name` / `long_name`                                            | `profile2.name`                                              | Finnhub only has one name field, both columns get the same value |
| `exchange`                                                            | `profile2.exchange`                                          |                                                                  |
| `quote_type`                                                          | —                                                            | Always `NULL` (not on free tier)                                 |
| `sector`                                                              | —                                                            | Always `NULL` — Finnhub only exposes `industry`                  |
| `industry`                                                            | `profile2.finnhubIndustry`                                   |                                                                  |
| `country` / `currency` / `website`                                    | `profile2.*`                                                 |                                                                  |
| `market_cap`                                                          | `profile2.marketCapitalization * 1e6`                        | Finnhub reports in millions                                      |
| `beta`, `trailing_pe`                                                 | `metric.beta`, `metric.peBasicExclExtraTTM`                  |                                                                  |
| `forward_pe`                                                          | —                                                            | Always `NULL` (not on free tier)                                 |
| `fifty_two_week_high/low`, `fifty_day_average`, `two_hundred_day_avg` | `metric.52WeekHigh/Low`, `metric.50DayMA`, `metric.200DayMA` |                                                                  |
| `shares_outstanding`                                                  | `profile2.shareOutstanding * 1e6`                            |                                                                  |
| `dividend_yield`                                                      | `metric.dividendYieldIndicatedAnnual`                        |                                                                  |
| `fetched_at`                                                          | job run time (UTC)                                           | `NOT NULL`                                                       |

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable                                | Required | Default                                        | Description                                                                                                                                    |
| --------------------------------------- | -------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `GRAVITINO_URI`                         | Yes      | —                                              | Gravitino Iceberg REST endpoint, e.g. `http://openhouse-gravitino:9001`                                                                        |
| `MINIO_ENDPOINT`                        | Yes      | —                                              | MinIO S3 endpoint                                                                                                                              |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | Yes      | —                                              | MinIO credentials (from `spark-app-secrets`)                                                                                                   |
| `GRAVITINO_OAUTH_CLIENT_SECRET`         | Yes      | —                                              | Keycloak `spark` client secret (from `spark-app-secrets`)                                                                                      |
| `GRAVITINO_OAUTH_SERVER_URI`            | No       | `http://openhouse-keycloak`                    | Keycloak base URL for the token endpoint                                                                                                       |
| `GRAVITINO_OAUTH_TOKEN_PATH`            | No       | `realms/iceberg/protocol/openid-connect/token` |                                                                                                                                                |
| `GRAVITINO_OAUTH_SCOPE`                 | No       | `gravitino`                                    |                                                                                                                                                |
| `FINNHUB_API_KEY`                       | Yes      | —                                              | Finnhub API key (from `spark-app-secrets`)                                                                                                     |
| `SYMBOLS_FILE`                          | No       | `/tmp/symbols.txt`                             | Path to the symbols list, mounted from the `company-info-loader-symbols` ConfigMap (one ticker per line, `#`-comments and blank lines ignored) |
| `OUTPUT_TABLE`                          | No       | `gravitino_bronze.raw.raw_company_info`        |                                                                                                                                                |

## Catalog / Connection Config

Registers a single Iceberg REST catalog `gravitino_bronze` pointing at the `bronze` warehouse via Gravitino's dynamic-catalog-provider (see `../../infra/k8s/storage/README.md`), authenticated with OAuth2 client-credentials against Keycloak. S3A is configured against MinIO with path-style access. See `CatalogConfigurator.scala` for the exact `spark.sql.catalog.*` keys — this same shape is reused (with a different catalog/warehouse name) across every app in this repo.

## Kubernetes Resource Sizing

From `k8s/company-info-loader-spark-application.yaml`:

- **Driver**: 1 core, 512Mi memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, 512Mi memory + 256Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval
- **`timeToLiveSeconds`**: 3600 (driver pod garbage-collected 1h after completion)
- Low parallelism tuning (`spark.sql.shuffle.partitions=2`) since the whole table is ~50 rows

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-company-info-loader.sh v0.4
./scripts/run-company-info-loader.sh
./scripts/stop-company-info-loader.sh
```

> ⚠️ `scripts/build-and-push-company-info-loader.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/company-info-loader-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires the `company-info-loader-symbols` ConfigMap and `spark-app-secrets` Secret to already exist — see the [top-level README](../README.md#prerequisites-required-before-running-any-spark-app).

## Known Issues

None specific to this app beyond the general prerequisites in the top-level README.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
