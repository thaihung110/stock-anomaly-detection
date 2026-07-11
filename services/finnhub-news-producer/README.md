# finnhub-news-producer

Polls Finnhub's `/company-news` REST endpoint for every configured symbol on a fixed interval, deduplicates articles in-memory, and publishes them to Kafka topic `raw.stock.news` — the fast-moving Bronze source the LLM Agent reads for "recent news" context.

## Data Flow

```
Finnhub REST /company-news (polled once per symbol per cycle)
    ↓ MD5(url) dedup against an in-memory seen-set
normalize() — map Finnhub fields → NewsArticle
    ↓
raw.stock.news (Kafka, key = symbol)
```

Polling loop, not a stream — one full pass over all symbols per cycle, then sleeps out the remainder of `poll_interval_sec`.

## Pipeline Steps

`finnhub_client.py` → `normalizer.py` → `producer.py`, orchestrated by `_run()` in `main.py`:

1. **`poll_news`** (`finnhub_client.py`): for each symbol in `symbols_list`, calls `/company-news?symbol=...&from=...&to=...` with a `[today - lookback_days, today]` window. Retries once with a 5s delay on HTTP `429`/`500`/`502`/`503` or a network error; any other non-200 status is logged and treated as zero results (no retry, no crash).
2. **Rate limiting**: a fixed `request_delay_sec` (default 1.1s) sleep between symbols — 50 symbols × 1.1s ≈ 55s per cycle, deliberately under Finnhub's free-tier 60 req/min cap.
3. **In-memory dedup**: computes `MD5(url)` per article and skips it if already in the `seen_ids` set built up across cycles within the process's lifetime. If the set exceeds `dedup_max_size` (default 10,000), it is **cleared entirely** and reseeded with just the current article — this resets the entire in-memory memory, not just the oldest entries (see Known Issues).
4. **`normalize`** (`normalizer.py`): maps Finnhub's `headline`/`summary`/`url`/`source`/`category`/`datetime` (Unix seconds) to `NewsArticle`, converting `datetime` → `published_at_ms` (×1000). Raises `KeyError` if `url` or `headline` is missing — caught in `main.py` and the article is dropped.
5. **`NewsProducer.publish`** (`producer.py`): `AIOKafkaProducer` with `acks="all"`, `enable_idempotence=True`, keyed by symbol.
6. **Cycle pacing**: after a full symbol pass, sleeps `max(0, poll_interval_sec - cycle_duration)` so cycles start on a roughly fixed cadence regardless of how long the pass itself took.

## Kafka Output Contract

Topic: **`raw.stock.news`** · Partition key: `symbol.encode("utf-8")`.

`NewsArticle` (`schema.py`) — also the source-of-truth contract consumed by `spark-application/news-ingest-stream`:

| Field | Type | Notes |
|---|---|---|
| `article_id` | str | `MD5(url)` — dedup key |
| `symbol` | str | Uppercased |
| `headline` | str | Non-empty, trimmed |
| `summary` | str \| null | |
| `url` | str | |
| `source` | str | e.g. `"Reuters"` |
| `category` | str \| null | |
| `published_at_ms` | int | Epoch **milliseconds** — validator rejects values that look like seconds |
| `fetched_at_ms` | int | Epoch milliseconds, set at normalize-time |

## Configuration

Env vars read by `Settings` (`config.py`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `FINNHUB_API_KEY` | **Yes** | — | |
| `KAFKA_BOOTSTRAP_SERVERS` | No | `localhost:9092` | |
| `KAFKA_TOPIC` | No | `raw.stock.news` | |
| `KAFKA_COMPRESSION_TYPE` | No | `gzip` | |
| `SYMBOLS` | No | 50 hardcoded tickers | Comma-separated |
| `POLL_INTERVAL_SEC` | No | `300.0` | 5 min; overridden to `300` in k8s |
| `REQUEST_DELAY_SEC` | No | `1.1` | Per-symbol delay, stays under 60 req/min |
| `LOOKBACK_DAYS` | No | `7` | Overridden to `2` in k8s |
| `DEDUP_MAX_SIZE` | No | `10000` | See Known Issues for the reset-on-overflow behavior |

## Kubernetes Resource Sizing

From `k8s/finnhub-news-producer/deployment.yaml`:

- 1 replica · **Requests**: 100m CPU, 128Mi memory · **Limits**: 250m CPU, 256Mi memory
- `initContainer: wait-for-kafka` — blocks pod start until `openhouse-kafka:9092` is reachable
- `FINNHUB_API_KEY` sourced from `finnhub-trades-producer-secrets` (shared secret with the trades producer — same Finnhub account/key covers both REST and WebSocket usage)

## Build & Run

```bash
cd services
./scripts/build_and_push-finnhub-news-producer.sh v0.2
./scripts/run-finnhub-news-producer.sh
./scripts/stop-finnhub-news-producer.sh
```

> ⚠️ `scripts/build_and_push-finnhub-news-producer.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/finnhub-news-producer/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

No upstream dependency besides Kafka. Requires a `finnhub-trades-producer-secrets` Kubernetes Secret with `FINNHUB_API_KEY` to already exist (see `finnhub-trades-producer/README.md`).

## Known Issues

- **Dedup-set overflow clears everything, not just the oldest entries.** When `seen_ids` exceeds `dedup_max_size`, the code does `seen_ids.clear()` then re-adds only the current article — every previously-seen `article_id` is forgotten in one shot. If Finnhub returns an already-published article again on the very next cycle (common, since `/company-news` always returns the full lookback window, not just new items), it will be **re-published as a duplicate** right after a clear. With 50 symbols and a `lookback_days` of a few days, hitting 10,000 unique articles before a restart is unlikely in practice, but the failure mode is a full dedup reset, not a graceful LRU evict.
- Every poll cycle re-fetches the **entire lookback window** for every symbol (no `since` cursor) — Finnhub's response volume, and therefore this producer's dedup workload, scales with `lookback_days`, not with actual new-article count.

## Testing

No automated tests — no `tests/` directory in this service.
