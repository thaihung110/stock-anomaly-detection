# llm-agent

Layer 1 of the detection pipeline: consumes every `alerts.raw` event, asks an LLM whether public news explains the anomaly, and republishes a `ConfirmedAlertEvent` to `alerts.confirmed` with the verdict attached. Also runs a background re-check for anomalies the LLM couldn't explain at alert time.

> **Note on `CLAUDE.md`**: the project-level architecture doc describes this stage as `data_conversion → [news_research ‖ data_crosscheck] → aggregation → routing` with `NEWS_EXPLAINED`/`UNEXPLAINED`/`DATA_ERROR` outcomes. The actual implementation below is simpler and has evolved past that description — it's a linear `ingest → retrieve_news → classify → (schedule_recheck | END)` LangGraph with no crosscheck node, and judgements are `EXPLAINED`/`UNEXPLAINED`/`UNCERTAIN` (no `DATA_ERROR`). **Every** judgement — including `EXPLAINED` — is published to `alerts.confirmed`; filtering/routing by judgement happens downstream in `alert-service`, not here.

## Architecture

```
alerts.raw (Kafka, AlertEvent)
    ↓ dedup_cache.is_seen(alert_id)? → skip if duplicate
LangGraph: ingest → retrieve_news → classify → route
    │           │            │
    │           │            └─ circuit breaker OPEN → fail to UNCERTAIN
    │           └─ fetch_news(symbol): union(bronze tail, silver history), dedup, top-K
    │
    └─ UNEXPLAINED → schedule_recheck (in-memory queue, fires once at +RECHECK_DELAY_MIN)
                          ↓
                      alerts.followup (Kafka, FollowUpEvent) — only on FLIP or CONFIRM

alerts.confirmed (Kafka, ConfirmedAlertEvent) ← every judgement, always published
```

Whole pipeline runs under an `agent_ttl_sec` (default 8s) timeout — **fail-open**: a timeout or any unhandled exception still publishes a `ConfirmedAlertEvent` with `llm_judgement=UNCERTAIN` rather than dropping the alert silently.

## Pipeline Steps

### Startup (`main.py` lifespan)

1. `build_llm(cfg)` (`llm/factory.py`) — provider-agnostic via LangChain's `init_chat_model("provider:model", temperature=0).with_structured_output(ClassifyResult)`. Switching LLM provider is a one-line env change (`LLM_MODEL`), no code edit — supports `google_genai:*`, `openai:*`, `anthropic:*` (provider package must be installed as an optional extra).
2. Builds `classify_chain = CLASSIFY_PROMPT | llm_client` **once** and shares it between the graph's `classify` node and the `recheck_queue` background worker.
3. Constructs `DedupCache` (TTL-based, lazy eviction on access) and `CircuitBreaker` (5 consecutive failures → OPEN for 60s → HALF_OPEN probe).
4. If `RECHECK_ENABLED`, starts a `RecheckQueue` background `asyncio.Task` that drains follow-up work items for the service's lifetime.

### Per-alert handling (`handle_alert`)

1. **Dedup check**: if `alert_id` was processed within `dedup_cache_ttl_sec` (default 900s), skip entirely — protects against Kafka redelivery causing a double-classify.
2. **`ingest` node**: logs receipt, resets all classification-result state fields to null/empty.
3. **`retrieve_news` node** → `fetch_news()` (`infrastructure/news_reader.py`): queries **two** Iceberg catalogs in parallel intent (sequential try/except, each independently fault-tolerant):
   - **Bronze** `raw.raw_news_articles` (Finnhub streaming, ~30s lag) within `NEWS_LOOKBACK_HOURS` (default 6h) — the fresh tail.
   - **Silver** `normalized.news_clean` (NewsAPI batch, cleaned+deduped) within `NEWS_LOOKBACK_DAYS` (default 3d) — the historical body.
   - Union (fresh articles take dedup precedence), dedup by `url` (fallback `md5(title)`), sort `published_at` DESC, truncate to `NEWS_TOP_K` (default 8).
   - Either catalog failing independently logs a warning and proceeds with whatever the other source returned — a Bronze outage doesn't block classification using Silver history, and vice versa.
4. **`classify` node**: if the circuit breaker `is_open()`, fast-fails to `UNCERTAIN` without calling the LLM at all. Otherwise calls `classify_chain.ainvoke(prompt_vars)` (`llm/prompts.py`'s `CLASSIFY_PROMPT`, requiring `EXPLAINED`/`UNEXPLAINED`/`UNCERTAIN` + `relevant_titles` + `news_summary`). **Relevance gate**: `news_refs` is built only from titles present in the retrieved articles — the LLM cannot fabricate a reference to an article it wasn't given, even if it hallucinates a title.
5. **Routing**: `UNEXPLAINED` → `schedule_recheck` node enqueues a `RecheckTask` for `RECHECK_DELAY_MIN` (default 20 min) later; every other judgement → `END` directly.
6. `handle_alert` builds `ConfirmedAlertEvent` (all `AlertEvent` fields + `llm_judgement`/`final_explanation`/`news_summary`/`news_category`/`news_refs`) and publishes to `alerts.confirmed` **regardless of judgement** — then marks `alert_id` as seen in the dedup cache.

### Background re-check (`RecheckQueue.run`)

For each `UNEXPLAINED` alert, waits until `recheck_at` (idempotent — an `alert_id` already scheduled is never double-enqueued, and a full queue drops new tasks with a warning rather than blocking), re-fetches news, and re-classifies with the same prompt:
- New judgement `UNCERTAIN` → **stay silent**, no `FollowUpEvent` (not enough signal to revise the verdict).
- New judgement differs from original → **FLIP**, emit `FollowUpEvent`.
- New judgement matches original (still `UNEXPLAINED`) → **CONFIRM**, emit `FollowUpEvent` anyway (the window expired with still no explanation — this is itself useful signal downstream).
- LLM error during re-check → silent, no event (logged only).

## Kafka Contracts

| Topic | Direction | Schema | Consumer Group |
|---|---|---|---|
| `alerts.raw` | Consume | `AlertEvent` (mirrors rule-engine's — do not rename/add fields) | `llm-agent` |
| `alerts.confirmed` | Produce | `ConfirmedAlertEvent` (superset of `AlertEvent` — extra fields ignored by consumers not expecting them) | — |
| `alerts.followup` | Produce | `FollowUpEvent` (`ref_alert_id`, `prev_judgement`, `new_judgement`, `news_summary`, `news_refs`, `event_ts`, `rule_name`) | — |

`symbol` on every schema is validated against `^[A-Z0-9.\-]{1,10}$` — rejects injection-style values before they reach a PyIceberg row filter.

## HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | `{"status": "ok"}` |
| `/metrics` | GET | Prometheus ASGI app — `ALERTS_RECEIVED`, `ALERTS_CLASSIFIED{judgement=...}`, `FAIL_OPEN_TOTAL`, `CLASSIFY_LATENCY`, `NEWS_FETCHED` |

## Configuration

Env vars read by `Settings` (`config.py`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | No | `localhost:9092` | |
| `KAFKA_INPUT_TOPIC` / `KAFKA_CONSUMER_GROUP` | No | `alerts.raw` / `llm-agent` | |
| `KAFKA_OUTPUT_TOPIC` / `KAFKA_FOLLOWUP_TOPIC` | No | `alerts.confirmed` / `alerts.followup` | |
| `LLM_MODEL` | No | `google_genai:gemini-2.5-flash-lite` | `"provider:model"` — see factory.py |
| `LLM_ESCALATION_MODEL` | No | `""` | Reserved for HIGH-severity escalation; not currently wired into the graph |
| `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Yes (matching provider) | — | Only the key for the active provider is required |
| `AGENT_TTL_SEC` | No | `8.0` | Fail-open deadline for the whole graph invocation |
| `ICEBERG_CATALOG_URI` / `ICEBERG_OAUTH2_*` | Yes (credential) | Gravitino/Keycloak defaults | Shared across bronze + silver catalog registration |
| `BRONZE_CATALOG_NAME` / `BRONZE_WAREHOUSE` / `NEWS_TABLE` / `NEWS_LOOKBACK_HOURS` | No | `bronze` / `bronze` / `raw.raw_news_articles` / `6` | |
| `SILVER_CATALOG_NAME` / `SILVER_WAREHOUSE` / `NEWS_DIGEST_TABLE` / `NEWS_LOOKBACK_DAYS` | No | `silver` / `silver` / `normalized.news_clean` / `3` | |
| `NEWS_TOP_K` | No | `8` | Overridden to `20` in k8s |
| `S3_ENDPOINT` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_REGION` / `S3_PATH_STYLE_ACCESS` | Yes (keys) | MinIO defaults | |
| `RECHECK_ENABLED` / `RECHECK_DELAY_MIN` / `RECHECK_QUEUE_MAX_SIZE` | No | `true` / `20` / `1000` | |
| `DEDUP_CACHE_TTL_SEC` | No | `900` | |
| `CB_FAILURE_THRESHOLD` / `CB_RECOVERY_TIMEOUT_SEC` | No | `5` / `60.0` | |
| `HTTP_PORT` | No | `8081` | |

## Catalog / Connection Config

Registers **two** Iceberg warehouses under the same Gravitino REST endpoint and OAuth2 credential: `bronze` (fresh news tail) and `silver` (cleaned historical news) — both read-only from this service's perspective.

## Kubernetes Resource Sizing

From `k8s/llm-agent/deployment.yaml`:

- 1 replica · **Requests**: 200m CPU, 512Mi memory · **Limits**: 1000m CPU, 1Gi memory (highest request/limit of the 4 core services — LLM client + LangGraph + two Iceberg catalog connections)
- Liveness/readiness on `/health` (initial delay 30s/15s, period 15s/10s)
- Secret `llm-agent-secret` required: one of `OPENAI_API_KEY`/`GOOGLE_API_KEY`/`ANTHROPIC_API_KEY` (matching `LLM_MODEL`'s provider), `ICEBERG_OAUTH2_CREDENTIAL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`
- Switching provider: change `OPENAI_API_KEY` → `GOOGLE_API_KEY` in the Secret and `LLM_MODEL` in the ConfigMap — no rebuild needed
- Rollback path documented directly in the manifest: `kubectl set env deploy/alert-service DELIVERY_SOURCE=raw -n stock-anomaly-detection` reverts `alert-service` to consuming `alerts.raw` directly, bypassing this service entirely, with no restart of `llm-agent` required

```bash
kubectl create secret generic llm-agent-secret \
  -n stock-anomaly-detection \
  --from-literal=OPENAI_API_KEY="sk-..." \
  --from-literal=ICEBERG_OAUTH2_CREDENTIAL="<client_id>:<client_secret>" \
  --from-literal=S3_ACCESS_KEY_ID="<minio_access_key>" \
  --from-literal=S3_SECRET_ACCESS_KEY="<minio_secret_key>"
```

## Build & Run

```bash
cd services
./scripts/build_and_push-llm-agent.sh v0.5
./scripts/run-llm-agent.sh
./scripts/stop-llm-agent.sh
```

> ⚠️ `scripts/build_and_push-llm-agent.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/llm-agent/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires `bronze.raw_news_articles` and `silver.news_clean` tables to exist (produced by `spark-application/news-ingest-stream` and `spark-application/news-cleaner`) for news retrieval to return anything — the service still runs and fails open to `UNCERTAIN` if both are empty/unreachable, it just won't have news context to classify with.

## Known Issues

- **`alert-service` currently consumes `alerts.confirmed` unconditionally** (per its `deployment.yaml`, `DELIVERY_SOURCE=confirmed`) — meaning `CLAUDE.md`'s statement that "LLM Agent [is] not yet deployed" and ADR-002's bypass is **stale**; the rollback path exists (revert `DELIVERY_SOURCE=raw` on `alert-service`) but is not the current live configuration. Worth reconciling `CLAUDE.md` if this drifts further.
- **No crosscheck/data-validation node** in the actual graph — only a single LLM call classifies purely from news content; there is no independent "does this news actually match the price/volume magnitude" verification step separate from what the LLM itself reasons about in one shot.
- Dedup cache, circuit breaker, and recheck queue are all **in-memory, per-replica** state — same horizontal-scaling caveat as `rule-engine`: a second replica would have its own independent circuit breaker and dedup window, and `alert_id` dedup would only work within a single replica's traffic.
- `LLM_ESCALATION_MODEL` is read into `Settings` but not referenced anywhere in `graph/`, `main.py`, or `llm/factory.py` — the "escalate to a stronger model for HIGH severity" behavior described in the config comment is not implemented yet.

## Testing

Has a real `tests/` suite — run with `pytest` from `services/llm-agent/`:

- `test_graph.py` — full LangGraph wiring (ingest/retrieve_news/classify/routing)
- `test_circuit_breaker.py`, `test_dedup_cache.py`, `test_recheck_queue.py` — infra unit tests
- `test_news_reader.py` — bronze/silver union + dedup logic
- `test_llm.py`, `test_llm_factory.py`, `test_provider.py` — LLM client construction and structured-output parsing
- `test_publisher.py` — Kafka publish wrapper
- `test_schema.py`, `test_config.py` — contract validation and settings parsing
