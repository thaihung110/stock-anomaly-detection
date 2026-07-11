# news-cleaner

Cleans and deduplicates `bronze.raw_news_articles` into the Silver layer. Handles the fact that polling-based ingestion (`news-ingest-stream`) can pick up the same headline multiple times across polls.

## Data Flow

```
gravitino_bronze.raw.raw_news_articles
    ↓ readBronze (full table read)
transform (filter invalid, hash-dedup by headline, normalize source)
    ↓ overwritePartitions() — dynamic partition overwrite
gravitino_silver.normalized.news_clean
```

Batch job, not streaming.

## Pipeline Steps

`NewsCleanerPipeline` (`pipeline/NewsCleanerPipeline.scala`):

1. **`readBronze`**: full read of `raw_news_articles`, no incremental filter.
2. **`transform`**:
   - Drops rows with a null/blank `title` or `url`.
   - Computes `dedup_hash = md5(trim(title))` — the same news event polled multiple times from Finnhub typically produces byte-identical headlines, so hashing the trimmed title is the dedup key (not `article_id`, which may differ across polls/sources for the same story).
   - Normalizes `source_name` to lowercase/trimmed, so downstream `GROUP BY source_name` queries aren't split by casing inconsistencies.
   - Within each `dedup_hash` group, ranks rows by `fetched_at ASC` (ties broken by `article_id`) using a window function, and keeps only `rank = 1` — the **earliest-fetched** occurrence of each duplicate headline wins.
   - Recomputes `published_date` from `published_at` — the code comment notes bronze's own `published_date` may be stale or missing as a partition column.
3. **`writeToSilver`**: sets `spark.sql.iceberg.dynamic-partition-overwrite.enabled=true` at the session level, then writes with the DataFrameWriterV2 API — `df.writeTo(outputTable).option("distribution-mode","hash").overwritePartitions()`. This **replaces** every partition the output touches (typically `published_date`), rather than upserting row-by-row like the `MERGE INTO`-based apps. Re-running on the same day cleanly replaces that day's partition — idempotent, but note this is a different write mechanism from `ohlcv-daily-cleaner`'s `MERGE INTO`.

### Target table: `gravitino_silver.normalized.news_clean`

`article_id`, `dedup_hash`, `symbol`, `source_name`, `title`, `description`, `url`, `category`, `published_at`, `fetched_at`, `data_source`, `cleaned_at`, `published_date`.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app — see `company-info-loader/README.md` |
| `GRAVITINO_OAUTH_SERVER_URI` / `_TOKEN_PATH` / `_SCOPE` | No | `http://openhouse-keycloak` / `realms/iceberg/protocol/openid-connect/token` / `gravitino` | |
| `INPUT_TABLE` | No | `gravitino_bronze.raw.raw_news_articles` | |
| `OUTPUT_TABLE` | No | `gravitino_silver.normalized.news_clean` | |

## Catalog / Connection Config

Registers both `gravitino_bronze` (read) and `gravitino_silver` (write) — same pattern as `ohlcv-daily-cleaner`.

## Kubernetes Resource Sizing

From `k8s/news-cleaner-spark-application.yaml`:

- **Driver**: 1 core, 512Mi memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, 512Mi memory + 256Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- Lower `spark.sql.shuffle.partitions=4` than `ohlcv-daily-cleaner`'s 8 — "daily news volume is modest" per the YAML's own comment

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-news-cleaner.sh v0.5
./scripts/run-news-cleaner.sh
./scripts/stop-news-cleaner.sh
```

> ⚠️ `scripts/build-and-push-news-cleaner.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/news-cleaner-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Depends on `news-ingest-stream` having populated `bronze.raw_news_articles` first.

## Known Issues

Dedup is keyed on the **exact trimmed headline text**. Two genuinely different articles that happen to share an identical headline (rare, but possible for very generic titles) would be incorrectly collapsed into one row. Full-table read every run also means cost grows with total accumulated news volume, same caveat as `ohlcv-daily-cleaner`.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
