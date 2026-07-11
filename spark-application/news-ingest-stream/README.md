# news-ingest-stream

Structured Streaming job: consumes news articles from Kafka (published by `finnhub-news-producer`) and appends them to the Bronze layer.

## Data Flow

```
Kafka topic `raw.stock.news`  (finnhub-news-producer)
    ↓ Structured Streaming, micro-batch every 30s
parse JSON → filter → rename → watermark(10 min) → dedupe(article_id)
    ↓ append
gravitino_bronze.raw.raw_news_articles
```

**Streaming, long-running** — `query.awaitTermination()` blocks forever; this job has no `timeToLiveSeconds` in its SparkApplication spec (unlike the batch loaders) and is expected to run continuously.

## Pipeline Steps

`NewsPipeline` (`pipeline/NewsPipeline.scala`), called as `buildRawStream → transform → write`:

1. **`buildRawStream`**: subscribes to Kafka topic `raw.stock.news` (`INPUT_TOPIC`), `startingOffsets=earliest`, `maxOffsetsPerTrigger=5000`, `failOnDataLoss=false` (tolerates Kafka retention having already expired old offsets rather than crashing the query).
2. **`transform`**:
   - Parses the Kafka `value` bytes as JSON against `NewsSchema` — the schema mirrors the producer's Kafka contract (`finnhub-news-producer/schema.py`): `article_id`, `symbol`, `headline`, `summary`, `url`, `source`, `category`, `published_at_ms`, `fetched_at_ms`.
   - Drops rows with a null/empty `url` or `headline` — unprocessable without either.
   - Renames producer field names to canonical Bronze column names: `headline→title`, `summary→description`, `source→source_name`.
   - Converts `published_at_ms`/`fetched_at_ms` (epoch **milliseconds**, per the project's Kafka schema contract) to `TIMESTAMP` by dividing by 1000 before `from_unixtime`.
   - Derives `published_date` from `published_at`.
   - `withWatermark("published_at", "10 minutes")` + `dropDuplicates("article_id")` — bounds state store growth for the dedup operation to a 10-minute late-arrival window.
3. **`write`**: appends to the Iceberg table via the `iceberg` streaming sink, `outputMode("append")`, `Trigger.ProcessingTime(TRIGGER_INTERVAL)` (default 30s), with a dedicated `checkpointLocation` on MinIO.

A `BatchProgressListener` is registered on the `SparkSession` and logs one line per micro-batch: batch ID, input row count, current watermark, and Kafka end offsets — useful for confirming the stream is actually consuming, not just alive.

### Target table: `gravitino_bronze.raw.raw_news_articles`

`article_id`, `symbol`, `source_name`, `title`, `description`, `url`, `category`, `published_at`, `fetched_at`, `published_date`.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app — see `company-info-loader/README.md` |
| `KAFKA_BOOTSTRAP_SERVERS` | Yes | — | e.g. `openhouse-kafka:9092` |
| `CHECKPOINT_LOCATION` | Yes | — | e.g. `s3a://checkpoints/news-ingest-stream` — **must be unique per streaming app**, never share a checkpoint path between jobs |
| `OUTPUT_TABLE` | No | `gravitino_bronze.raw.raw_news_articles` | |
| `INPUT_TOPIC` | No | `raw.stock.news` | |
| `TRIGGER_INTERVAL` | No | `30 seconds` | Micro-batch interval |
| `KAFKA_MAX_OFFSETS_PER_TRIGGER` | No | `5000` | Caps how many Kafka messages one micro-batch can pull, bounding batch size/latency |
| `ICEBERG_WRITE_DISTRIBUTION_MODE` | No | `hash` | |
| `ICEBERG_TARGET_FILE_SIZE_BYTES` | No | `134217728` (128MB) | |
| `ICEBERG_FANOUT_ENABLED` | No | `false` (set to `true` in `k8s/`) | Iceberg fanout writer — needed when writing to multiple partitions per micro-batch without a global sort |

## Catalog / Connection Config

Same `gravitino_bronze` REST catalog pattern as `company-info-loader`, wired via `CatalogConfigurator.scala`. The SparkApplication YAML deliberately does **not** set `spark.sql.catalog.gravitino_bronze.*` in `sparkConf` — that's configured programmatically in code instead, "to avoid duplicated config sources" (per the YAML's own comment).

## Kubernetes Resource Sizing

From `k8s/news-ingest-stream-spark-application.yaml`:

- **Driver**: 1 core, 512Mi memory + 384Mi overhead
- **Executor**: 1 instance, 1 core, 768Mi memory + 384Mi overhead
- **`restartPolicy`**: `OnFailure`, **10** retries (higher than the batch jobs' 3 — a long-running stream is expected to recover from transient Kafka/network blips rather than fail fast), 10s interval
- **No `timeToLiveSeconds`** — unlike batch jobs, this pod is meant to run indefinitely
- Streaming-specific tuning: RocksDB state store provider (`spark.sql.streaming.stateStore.providerClass`), `compactOnCommit=true`, `stopGracefullyOnShutdown=true` (lets an in-flight micro-batch finish before pod termination), `minBatchesToRetain=5`

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-news-ingest-stream.sh v2.9
./scripts/run-news-ingest.sh
./scripts/stop-news-ingest.sh
```

> Note the script names don't follow the `<app-name>` pattern exactly — they're `run-news-ingest.sh` / `stop-news-ingest.sh`, not `run-news-ingest-stream.sh`.

> ⚠️ `scripts/build-and-push-news-ingest-stream.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/news-ingest-stream-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires `spark-app-secrets` and a running Kafka with topic `raw.stock.news` already created (see `orchestration/README.md#3-creating-kafka-topics`).

## Known Issues

None specific to this app beyond the general prerequisites in the top-level README. If the query dies and restarts, Spark Structured Streaming resumes from the checkpoint — do not delete `s3a://checkpoints/news-ingest-stream` unless you intend to reprocess from `startingOffsets=earliest` (which, combined with `failOnDataLoss=false`, is also the recovery path if the checkpoint itself is lost/corrupted).

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
