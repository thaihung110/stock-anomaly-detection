# services/

Real-time microservices for the Stock Anomaly Detection Platform: 3 data producers, the 2-layer detection pipeline (Rule Engine → LLM Agent), the sole Telegram sender, and the Telegram command bot. All async Python (FastStream/FastAPI), one Docker image per service, deployed to the `stock-anomaly-detection` Kubernetes namespace.

## Structure

```
services/
├── yfinance-quotes-producer/   # Yahoo Finance WebSocket → raw.stock.quotes
├── finnhub-trades-producer/    # Finnhub WebSocket → raw.stock.trades
├── finnhub-news-producer/      # Finnhub REST poll → raw.stock.news
├── rule-engine/                # Layer 0 — 6 system rules + custom user rules
├── llm-agent/                  # Layer 1 — LangGraph news-based classification
├── alert-service/               # Sole Telegram sender; fan-out + DLQ + audit trail
├── telegram-bot/                # /commands — custom alerts, watchlist, preferences
├── db/migrations/                # PostgreSQL schema (users, alert rules, watchlist, prefs)
├── k8s/<service>/deployment.yaml # One manifest per service
└── scripts/                     # build_and_push-*.sh / run-*.sh / stop-*.sh
```

Each service has its own README with full detail: architecture, pipeline steps, Kafka contracts, configuration, resource sizing, and known issues. This file is the index and covers what's shared across all of them.

## Architecture

```
yfinance-quotes-producer ──┐
finnhub-trades-producer ───┼─→ raw.stock.{quotes,trades,news}
finnhub-news-producer ─────┘         │
                                       ▼ (quotes only)
                                 rule-engine (Layer 0)
                          ┌──────────┴──────────┐
                    alerts.raw              alerts.user
                          │                       │
                     llm-agent (Layer 1)          │
                          │                       │
                 alerts.confirmed +               │
                 alerts.followup                  │
                          └──────────┬────────────┘
                                     ▼
                              alert-service
                         (sole Telegram sender)
                                     │
                              Telegram Bot API
                                     ▲
                              telegram-bot ──→ PostgreSQL (users, rules, watchlist, prefs)
                                     │
                          POST /internal/reload-*
                          (rule-engine, alert-service)
```

> **`CLAUDE.md` sync note**: the project's top-level `CLAUDE.md` currently describes the LLM Agent as *"(not yet deployed)"* and ADR-002 as an active bypass. The actual `k8s/alert-service/deployment.yaml` runs `DELIVERY_SOURCE=confirmed` with a comment noting *"ADR-002 lifted: llm-agent is deployed and publishing to alerts.confirmed"* — i.e. the LLM Agent **is** live in the current deployment, and `CLAUDE.md` is stale on this point. Each service's own README notes the same discrepancy where it's most relevant (`llm-agent/README.md`, `alert-service/README.md`).

## Applications

| Service | Role | Kafka In → Out | Image |
|---|---|---|---|
| `yfinance-quotes-producer` | Yahoo Finance quote stream | — → `raw.stock.quotes` | `<your-registry>/yfinance-quotes-producer:<tag>` |
| `finnhub-trades-producer` | Finnhub trade tick stream | — → `raw.stock.trades` | `<your-registry>/finnhub-trades-producer:<tag>` |
| `finnhub-news-producer` | Finnhub news poller | — → `raw.stock.news` | `<your-registry>/finnhub-news-producer:<tag>` |
| `rule-engine` | 6 system rules + custom user rules | `raw.stock.quotes` → `alerts.raw`, `alerts.user` | `<your-registry>/rule-engine:<tag>` |
| `llm-agent` | News-based LLM classification | `alerts.raw` → `alerts.confirmed`, `alerts.followup` | `<your-registry>/llm-agent:<tag>` |
| `alert-service` | Telegram delivery, audit trail, DLQ | `alerts.confirmed`\|`raw`, `alerts.user`, `alerts.followup` → `alerts.failed` | `<your-registry>/alert-service:<tag>` |
| `telegram-bot` | `/commands` — alerts, watchlist, prefs | — (HTTP webhook + Postgres) | `<your-registry>/telegram-bot:<tag>` |

`<your-registry>` is whatever `REGISTRY` you set in the matching `scripts/build_and_push-*.sh` — see [Build and Push Docker Image](#build-and-push-docker-image) below. `<tag>` is whatever version argument you pass that script (each service's own README shows the version currently referenced by its `k8s/<service>/deployment.yaml`, e.g. `v0.8` for `rule-engine`).

## Database

PostgreSQL is the OLTP store for custom alerts, watchlists, and preferences — shared by `rule-engine` (read/write `user_alert_rules`/`user_alert_events`), `alert-service` (read-only, subscriber routing), and `telegram-bot` (read/write, all tables). Apply both migrations before starting any of the three:

```bash
kubectl port-forward svc/openhouse-postgresql-primary 5432:5432 -n stock-anomaly-detection &

psql -h localhost -U stock_user -d stock_anomaly -f services/db/migrations/001_initial_schema.sql
psql -h localhost -U stock_user -d stock_anomaly -f services/db/migrations/002_multi_user_routing.sql

# Verify
psql -h localhost -U stock_user -d stock_anomaly -c "\dt"
# Expected: sync_watermarks, user_alert_events, user_alert_rules,
#           user_preferences, user_watchlist, users
```

| Migration | Adds |
|---|---|
| `001_initial_schema.sql` | `users`, `user_alert_rules`, `user_alert_events`, `sync_watermarks`; ENUMs `alert_operator`/`alert_field`/`alert_status`/`alert_frequency` |
| `002_multi_user_routing.sql` | `users.chat_id` (+ unique index), `user_watchlist`, `user_preferences` (+ `system_alert_mode` ENUM), auto-create-preferences trigger on new user, `updated_at` touch triggers |

`002_rollback.sql` reverses `002_multi_user_routing.sql` only — there is no rollback script for `001`.

## Kafka Topics

8 topics, all created before any service starts (see `infra/k8s/orchestration/scripts/create_kafka_topics_plaintext.sh`):

| Topic | Producer | Consumer |
|---|---|---|
| `raw.stock.quotes` | `yfinance-quotes-producer` | `rule-engine` |
| `raw.stock.trades` | `finnhub-trades-producer` | `spark-application/trades-ohlcv-stream` |
| `raw.stock.news` | `finnhub-news-producer` | `spark-application/news-ingest-stream` |
| `alerts.raw` | `rule-engine` (system rules) | `llm-agent` (or `alert-service` directly if `DELIVERY_SOURCE=raw`) |
| `alerts.user` | `rule-engine` (custom rules) | `alert-service` |
| `alerts.confirmed` | `llm-agent` | `alert-service` |
| `alerts.followup` | `llm-agent` (re-check worker) | `alert-service` |
| `alerts.failed` | `alert-service` (DLQ) | Operator replay tooling (manual) |

## Prerequisites

Kubernetes Secrets required per service (see each service's own README for the exact `kubectl create secret` command):

| Secret | Used by | Keys |
|---|---|---|
| `rule-engine-secret` | `rule-engine` | `ICEBERG_OAUTH2_CREDENTIAL`, `PG_PASSWORD`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` |
| `llm-agent-secret` | `llm-agent` | One of `OPENAI_API_KEY`/`GOOGLE_API_KEY`/`ANTHROPIC_API_KEY`, `ICEBERG_OAUTH2_CREDENTIAL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` |
| `alert-service-secret` | `alert-service` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ICEBERG_OAUTH2_CREDENTIAL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `PG_PASSWORD` |
| `telegram-bot-secret` | `telegram-bot` | `TELEGRAM_BOT_TOKEN`, `PG_PASSWORD` |
| `finnhub-trades-producer-secrets` | `finnhub-trades-producer`, `finnhub-news-producer` | `FINNHUB_API_KEY` (shared by both Finnhub producers) |

`yfinance-quotes-producer` needs no secret — Yahoo Finance's WebSocket feed is unauthenticated.

## Build and Push Docker Image

Every service has its own `scripts/build_and_push-<name>.sh`, all following the same shape:

```bash
#!/bin/bash
set -e

REGISTRY="hungvt0110"        # ← change this to your own registry
SERVICE_NAME="<service>"
TAG="${1:-latest}"
IMAGE_NAME="$REGISTRY/$SERVICE_NAME:$TAG"
SERVICES_DIR="$(dirname "$0")/.."

docker build -t "$IMAGE_NAME" -f "$SERVICES_DIR/<service>/Dockerfile" "$SERVICES_DIR"
docker push "$IMAGE_NAME"
```

Before your first build:

1. **Edit `REGISTRY`** in every `scripts/build_and_push-*.sh` you plan to use — it is hardcoded to `hungvt0110` (the original author's Docker Hub username) in all 7 scripts.
2. **Update the matching `image:` field** in `k8s/<service>/deployment.yaml` to reference your registry + the tag you just pushed.
3. **`docker login`** to your registry before pushing, if it requires authentication.

```bash
cd services
./scripts/build_and_push-rule-engine.sh v0.8
```

## Run / Stop Scripts

All scripts live in `scripts/` and take the form `run-<name>.sh` / `stop-<name>.sh` — apply/delete the corresponding `k8s/<name>/deployment.yaml` and wait for rollout. **Three script names do not match their service directory name:**

| Service directory | Script name actually used |
|---|---|
| `finnhub-trades-producer` | `build_and_push-finnhub-producer.sh`, `run-finnhub-producer.sh`, `stop-finnhub-producer.sh` |
| `yfinance-quotes-producer` | `build_and_push-yfinance-producer.sh`, `run-yfinance-producer.sh`, `stop-yfinance-producer.sh` |
| `finnhub-news-producer` | `build_and_push-finnhub-news-producer.sh` (matches — only the two above are shortened) |

The other 4 services (`rule-engine`, `llm-agent`, `alert-service`, `telegram-bot`) have scripts that match their directory name exactly.

## First-Time Startup Order

1. **Infra**: PostgreSQL, Kafka, MinIO, Gravitino, Keycloak already running in `stock-anomaly-detection` (see `infra/k8s/`).
2. **DB migrations**: apply `001_initial_schema.sql` → `002_multi_user_routing.sql` (see [Database](#database) above).
3. **Kafka topics**: create all 8 topics.
4. **Secrets**: create all 5 secrets listed in [Prerequisites](#prerequisites).
5. **Producers**: `yfinance-quotes-producer`, `finnhub-trades-producer`, `finnhub-news-producer` — no dependency on any other service in this directory.
6. **`rule-engine`**: needs `gold.rule_engine_context` populated (Spark `rule-engine-context-builder`) and `gold.dim_symbol`/`raw_company_info` for context to be non-empty.
7. **`llm-agent`**: needs `bronze.raw_news_articles`/`silver.news_clean` for news context (still runs and fails open to `UNCERTAIN` without them).
8. **`alert-service`**: needs `rule-engine` and/or `llm-agent` already producing to whichever topic `DELIVERY_SOURCE` points at.
9. **`telegram-bot`**: needs `rule-engine` and `alert-service` reachable for hot-reload calls (both fail soft — a reload failure logs a warning, it doesn't block the bot), and a public HTTPS tunnel for the Telegram webhook.

## Known Issues (cross-cutting)

- **`CLAUDE.md`'s Services section is stale on LLM Agent deployment status** — see the architecture note above. Worth reconciling if `CLAUDE.md` continues to drift from the live k8s manifests.
- **`services/shared/` is an empty, vestigial directory.** Several Dockerfiles/comments reference a pre-ADR-001 `shared/telegram_client` module that no longer exists (`alert-service` is now the sole Telegram sender) — the directory itself was never removed.
- **3 build/run/stop script names don't match their service directory** — see [Run / Stop Scripts](#run--stop-scripts) above. Easy to `cd` into the wrong place looking for `run-finnhub-trades-producer.sh` or `run-yfinance-quotes-producer.sh` — neither exists.
- **In-memory, per-replica state is common across all 4 core services** — `rule-engine`'s cooldowns/prev-values, `llm-agent`'s dedup cache/circuit breaker/recheck queue, `alert-service`'s rate limiter/subscriber cache, all currently rely on the single-replica (`replicas: 1`) deployment posture in every `k8s/*/deployment.yaml`. None of these services are safe to scale horizontally without further work — each service's own README calls this out specifically.

## Testing

Coverage varies significantly by service — see each README's own Testing section for exact file lists:

| Service | Has `tests/`? | Depth |
|---|---|---|
| `yfinance-quotes-producer` | No | — |
| `finnhub-trades-producer` | No (`.pytest_cache/` only, no source) | — |
| `finnhub-news-producer` | No | — |
| `rule-engine` | Yes | All 6 rules, custom rules, cooldowns, DB repository |
| `llm-agent` | Yes | Full LangGraph wiring, circuit breaker, dedup, recheck, news retrieval |
| `alert-service` | Yes | Most extensive — consumers, delivery, formatter, rate limiter, Iceberg writers, DLQ, admin API |
| `telegram-bot` | Yes | Thin — `/start`, symbol validation, watchlist/preference services only; no handler or HTTP-client tests yet |

Run any service's suite with `pytest` from that service's directory (e.g. `cd services/rule-engine && pytest`).
