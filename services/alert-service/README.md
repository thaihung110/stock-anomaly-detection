# alert-service

The **sole Telegram sender** in the platform (ADR-001) — every system alert and every custom user alert flows through here before reaching a chat. Owns audit-trail writes to `gold.fact_alert_history`, rate-limits all outbound Telegram traffic, and dead-letters anything it cannot deliver.

## Architecture

```
alerts.raw  (AlertEvent)        ─┐
alerts.confirmed (ConfirmedAlertEvent) ─┼─→ handle_alert / handle_confirmed  (exactly one registered,
alerts.followup (FollowUpEvent) ─┘       picked by DELIVERY_SOURCE at import time)
alerts.user (CustomAlertEvent)  ──→ handle_custom_alert
                                        │
                                        ▼
                          AlertDeliveryService
                    ┌──────────────┼──────────────┐
             fan_out / admin_only   deliver_custom   deliver_followup
                    │                    │                  │
         write gold.fact_alert_history  (skipped —          (skipped — update to
         history FIRST, then Telegram    Spark sync_custom_  an already-delivered
                    │                    alerts owns this)   alert, no new fact row)
             PerChatRateLimiter.acquire()
                    │
             SharedTelegramClient.send_message()
                    │
          failure ──┴──→ DLQPublisher → alerts.failed
```

Two independent feature toggles reshape this at startup: `DELIVERY_SOURCE` (which system-alert topic/schema is consumed) and `ENABLE_FANOUT` (single admin chat vs. per-subscriber routing). Both live entirely in `core/config.py` and are read once at import time in `consumers/`.

## Pipeline Steps

### System alerts (`consumers/system_alerts.py`)

Exactly **one** handler is registered, chosen by `cfg.delivery_source` at **module import time** (not per-message):

- `DeliverySource.CONFIRMED` (current live config) → `handle_confirmed` subscribes to `alerts.confirmed`, parses `ConfirmedAlertEvent`, renders with `format_confirmed_message` (HTML, includes the "AI Analysis" block) — **every judgement is delivered**, including `EXPLAINED`; there is no "log only, don't send" branch for explained anomalies in the current code.
- `DeliverySource.RAW` → `handle_alert` subscribes to `alerts.raw`, parses `AlertEvent`, renders with legacy `format_message` (Markdown).

Either way, delivery goes through `fan_out` (if `ENABLE_FANOUT=true`) or `deliver_admin_only` (single admin chat) — both implemented once in `AlertDeliveryService` so the two modes cannot drift apart. When `DELIVERY_SOURCE=confirmed`, a best-effort (never blocks delivery) `gold.anomaly_judgement` row is appended afterward via `JudgementWriter.append_initial`.

### `AlertDeliveryService.fan_out` / `.deliver_admin_only` (`services/delivery.py`)

**Ordering invariant, always in this order:**
1. Resolve recipients — `fan_out` looks up `SubscriberCache.get(symbol)` (Postgres-backed, TTL-cached); `deliver_admin_only` always targets `cfg.telegram.chat_id`.
2. **Write `gold.fact_alert_history` first** — one batched Iceberg commit covering every recipient (or the single admin row with `user_id=NULL`). If this write fails (not a timeout), the whole delivery **aborts** and the failure is DLQ'd — Telegram is never contacted for an alert that isn't durably recorded yet.
   - A `TimeoutError` from the writer is treated specially: the commit may have actually succeeded in the background thread, so it is **not** DLQ'd (that would risk a duplicate row on replay) — delivery just aborts silently for this alert.
3. Only after the history write succeeds: acquire the rate limiter, then `send_message` per recipient. In fan-out mode, all recipients are sent to concurrently via `asyncio.gather`; a single recipient's Telegram failure is logged and DLQ'd without aborting the others.
4. Message rendering is picked per exact event type (`ConfirmedAlertEvent` → HTML with AI analysis block; plain `AlertEvent` → legacy Markdown) via a `type(event)`-keyed dict, so adding a new alert subtype only means adding a dict entry, not touching `fan_out`'s control flow.

If `fan_out` finds zero matching recipients (everyone opted out), the alert is **silently dropped** — deliberately no admin-chat fallback in fan-out mode (the admin must opt in with `system_alert_mode=ALL` like anyone else).

### Custom alerts (`consumers/custom_alerts.py` → `deliver_custom`)

Consumes `alerts.user` (`CustomAlertEvent`), formats as plain text (`format_custom_message` — no Markdown/HTML escaping since field/operator strings come from a fixed enum, not free text), and routes:
- `ENABLE_PER_USER_ROUTING=true` → `event.chat_id` (falls back to the admin chat with a warning if `chat_id` is `null`, e.g. the user never ran `/start`).
- `ENABLE_PER_USER_ROUTING=false` → always the admin chat.

**Never writes `gold.fact_alert_history`** — that row is written by the Spark `sync_custom_alerts` job (07:30 UTC) reading `user_alert_events`; writing it here too would double the row per the project's OLTP→OLAP bridge contract (see `spark-application/sync-custom-alerts/README.md`).

### Follow-ups (`consumers/followups.py` → `deliver_followup`)

Only registered when `DELIVERY_SOURCE=confirmed` (the LLM Agent is the sole producer of `alerts.followup` — nothing to consume otherwise). A `FollowUpEvent` is an update to an **already-delivered** alert, so it never writes `fact_alert_history` — only the opt-in `gold.anomaly_judgement` analytics row (`revision=1`, `is_flip` set when the verdict changed).

## Feature Flags

| Flag | Effect when `true` | Effect when `false` (default in code, but see current k8s config) |
|---|---|---|
| `DELIVERY_SOURCE` | `confirmed` — consume `alerts.confirmed`, render AI-analysis block, write `anomaly_judgement` | `raw` — consume `alerts.raw`, legacy Markdown, no judgement analytics |
| `ENABLE_FANOUT` | Per-subscriber routing via Postgres `user_preferences`/`user_watchlist`; requires `pg_pool` + `SubscriberCache` to be constructed at startup | Single admin chat only (`cfg.telegram.chat_id`) — legacy behavior |
| `ENABLE_PER_USER_ROUTING` | Custom alerts route to the firing user's own `chat_id` | Custom alerts always go to the admin chat |
| `WATCHLIST_GATING` | *(read into `Settings` but not referenced anywhere in the codebase — dead flag, see Known Issues)* | — |
| `DLQ_ENABLED` | Failed deliveries/history-writes are appended to `alerts.failed` for replay | Failures are logged only, no DLQ record |

**Current live k8s config** (`k8s/alert-service/deployment.yaml`) runs `DELIVERY_SOURCE=confirmed`, `ENABLE_FANOUT=true` — i.e. the full multi-user, LLM-validated path, not the flags' code-level defaults (`raw`/`false`).

## Kafka Contracts

| Topic | Direction | Schema | Consumer Group |
|---|---|---|---|
| `alerts.confirmed` (or `alerts.raw`, per `DELIVERY_SOURCE`) | Consume | `ConfirmedAlertEvent` (or `AlertEvent`) | `alert-service-confirmed` (or `alert-service`) |
| `alerts.user` | Consume | `CustomAlertEvent` | `alert-service-user` |
| `alerts.followup` | Consume (only if `DELIVERY_SOURCE=confirmed`) | `FollowUpEvent` | `alert-service-followup` |
| `alerts.failed` | Produce (if `DLQ_ENABLED`) | `FailedAlertEnvelope` (`original_event`, `recipient`, `reason: DLQReason`, `error`, `failed_at_ms`, `attempt_count`) | — |

`DLQReason` values: `rate_limit` (429 exhausted), `permanent` (non-429 4xx), `transport` (timeout/5xx/network exhausted), `history_write` (Iceberg append failed — not on timeout), `subscriber_lookup` (Postgres error resolving fan-out recipients).

## HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | `{"status": "ok"}` |
| `/internal/reload-subscribers` | POST | Invalidates `SubscriberCache` (all entries + cancels in-flight fetches) — called by Telegram Bot after any `user_preferences`/`user_watchlist` mutation. Returns `409` with `{"status": "noop", "reason": "fanout_disabled"}` if the cache doesn't exist (fan-out off). |

## Configuration

Env vars read by `Settings` (`core/config.py`) — grouped by the read-only view properties (`cfg.kafka`, `cfg.telegram`, `cfg.iceberg`, `cfg.postgres`) that code actually consumes, though the env vars themselves stay flat:

| Variable | Required | Default | Description |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | No | `localhost:9092` | |
| `KAFKA_INPUT_TOPIC` / `KAFKA_CONSUMER_GROUP` | No | `alerts.raw` / `alert-service` | Overridden to `alerts.confirmed` / `alert-service-confirmed` in k8s |
| `KAFKA_USER_ALERT_TOPIC` / `KAFKA_USER_CONSUMER_GROUP` | No | `alerts.user` / `alert-service-user` | |
| `DELIVERY_SOURCE` | No | `raw` | `raw` \| `confirmed` |
| `KAFKA_CONFIRMED_TOPIC` / `_CONSUMER_GROUP` | No | `alerts.confirmed` / `alert-service-confirmed` | |
| `KAFKA_FOLLOWUP_TOPIC` / `_CONSUMER_GROUP` | No | `alerts.followup` / `alert-service-followup` | |
| `WATCHLIST_GATING` | No | `false` | Unused — see Known Issues |
| `TELEGRAM_BOT_TOKEN` | **Yes** | — | |
| `TELEGRAM_CHAT_ID` | **Yes** | — | `int` (private/group chat) or `str` (`@channel_username`) |
| `TELEGRAM_API_BASE_URL` | No | `https://api.telegram.org` | |
| `TELEGRAM_RETRY_ATTEMPTS` / `_RETRY_BASE_DELAY` | No | `3` / `1.0` | Exponential backoff base |
| `TELEGRAM_GLOBAL_RATE` / `_PER_CHAT_RATE` | No | `25.0` / `1.0` msg/s | Proactive rate limiting, under Telegram's ~30/s and ~1/s ceilings |
| `RATE_LIMITER_CACHE_SIZE` / `_TIME_PERIOD` | No | `10000` / `1.0` | Bounded LRU of per-chat buckets |
| `ICEBERG_CATALOG_NAME` / `_URI` / `_OAUTH2_*` / `_WAREHOUSE` | Yes (credential) | `stock_catalog` / Gravitino defaults / `gold` | |
| `FACT_ALERT_HISTORY_TABLE` | No | `gold.fact_alert_history` | |
| `ANOMALY_JUDGEMENT_TABLE` / `JUDGEMENT_WRITE_TIMEOUT_SEC` | No | `gold.anomaly_judgement` / `10.0` | Only created/written when `DELIVERY_SOURCE=confirmed` |
| `S3_ENDPOINT` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_REGION` / `S3_PATH_STYLE_ACCESS` | Yes (keys) | MinIO defaults | |
| `ENABLE_FANOUT` | No | `false` | |
| `SUBSCRIBER_CACHE_TTL_SEC` | No | `60.0` | |
| `PG_HOST` / `PG_PORT` / `PG_DATABASE` / `PG_USER` / `PG_PASSWORD` | Yes (password, if `ENABLE_FANOUT`) | `localhost` / `5432` / `stock_anomaly` / `stock_user` / — | |
| `ENABLE_PER_USER_ROUTING` | No | `false` | |
| `DLQ_ENABLED` / `ALERTS_FAILED_TOPIC` | No | `true` / `alerts.failed` | |
| `APP_PORT` | No | `8080` | |

## Catalog / Connection Config

Registers `gravitino_gold` for both writers: `HistoryWriter` (`gold.fact_alert_history`, always active) and `JudgementWriter` (`gold.anomaly_judgement`, self-creates the namespace/table on first init **only** when `DELIVERY_SOURCE=confirmed` — a no-op otherwise, so the table never appears in raw mode). Both writers serialize all commits through a **single-worker thread executor** each — this eliminates the `CommitFailedException` race that occurred when concurrent fan-out recipients each triggered their own concurrent Iceberg append on the same table handle.

## Kubernetes Resource Sizing

From `k8s/alert-service/deployment.yaml`:

- 1 replica · **Requests**: 100m CPU, 256Mi memory · **Limits**: 500m CPU, 512Mi memory
- Liveness/readiness on `/health` (initial delay 30s/15s, period 15s/10s)
- Secret `alert-service-secret` required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ICEBERG_OAUTH2_CREDENTIAL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `PG_PASSWORD`

```bash
kubectl create secret generic alert-service-secret \
  -n stock-anomaly-detection \
  --from-literal=TELEGRAM_BOT_TOKEN="<bot_token>" \
  --from-literal=TELEGRAM_CHAT_ID="<chat_id>" \
  --from-literal=ICEBERG_OAUTH2_CREDENTIAL="<client_id>:<client_secret>" \
  --from-literal=S3_ACCESS_KEY_ID="<minio_access_key>" \
  --from-literal=S3_SECRET_ACCESS_KEY="<minio_secret_key>" \
  --from-literal=PG_PASSWORD="<pg_password>"
```

## Build & Run

```bash
cd services
./scripts/build_and_push-alert-service.sh v1.1
./scripts/run-alert-service.sh
./scripts/stop-alert-service.sh
```

> ⚠️ `scripts/build_and_push-alert-service.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/alert-service/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires `db/migrations/001_initial_schema.sql` and `002_multi_user_routing.sql` applied (for `users`/`user_preferences`/`user_watchlist` — only load-bearing when `ENABLE_FANOUT=true`), and either `rule-engine` (raw mode) or `llm-agent` (confirmed mode) already producing to the topic this service consumes.

## Known Issues

- **`WATCHLIST_GATING` is a dead config flag** — declared in `Settings` and documented in the ConfigMap comment ("MEDIUM+EXPLAINED alerts delivered only to watchlist subscribers") but never read anywhere in `consumers/`, `services/`, or `container.py`. Setting it currently has no effect.
- **Every LLM judgement is delivered, including `EXPLAINED`.** There is no "explained anomalies are logged but not sent" behavior in the current code — `handle_confirmed` calls `fan_out`/`deliver_admin_only` unconditionally regardless of `event.llm_judgement`. If reducing noise from explained-but-still-anomalous moves is desired, that filter does not exist today.
- A `TimeoutError` from either Iceberg writer intentionally skips the DLQ (to avoid a duplicate row if the commit actually succeeded in the background) — but this also means a persistent Iceberg slowness silently drops alerts from `fact_alert_history` with no operator-visible failure record, only a `*_timeout_unknown_state` log line.
- `PerChatRateLimiter` and `SubscriberCache` are both **process-local, in-memory** state — consistent with the current 1-replica deployment, but a second replica would each enforce Telegram's rate ceiling independently (risking a combined rate above what Telegram allows) and cache subscribers separately (a `/internal/reload-subscribers` call only invalidates the replica that receives the HTTP request).

## Testing

Has the most extensive `tests/` suite of the 4 core services — run with `pytest` from `services/alert-service/`:

- `tests/consumers/` — `system_alerts` (both raw and confirmed variants), `custom_alerts`, `followups`
- `tests/services/` — `delivery` (fan-out, admin-only, custom, followup paths), `formatter`, `rate_limiter`, `subscriber_cache`
- `tests/infrastructure/iceberg/` — `history_writer`, `judgement_writer`, `base_writer`
- `tests/infrastructure/` — `dlq_producer`, `telegram_client`
- `tests/api/test_admin_router.py` — `/internal/reload-subscribers`, `/health`
- `test_bootstrap.py`, `test_container.py`, `test_main_lifespan.py`, `test_contract.py` — composition-root wiring and Kafka schema contract checks
