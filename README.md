# Stock Anomaly Detection Platform

The platform ingests live market data, detects statistical anomalies in real time,
asks an LLM _"is this move explained by public news?"_, and delivers an instant
Telegram alert with an AI verdict and source links. A lakehouse (Iceberg) records
every event for analytics; a daily Spark batch refreshes the statistical baselines
the rule engine uses.

---

## Table of Contents

- [Key Features](#key-features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Layout](#repository-layout)
- [Detection Pipeline](#detection-pipeline)
- [Data Lakehouse Layers](#data-lakehouse-layers)
- [Kafka Topics](#kafka-topics)
- [Custom Alerts](#custom-alerts)
- [Getting Started (Local Dev)](#getting-started-local-dev)
- [Deployment (Kubernetes)](#deployment-kubernetes)
- [Testing](#testing)
- [Documentation](#documentation)

---

## Key Features

- **Two-layer detection** — fast statistical rules (Layer 0) feed an LLM news-validation
  agent (Layer 1) that classifies each anomaly as `EXPLAINED` / `UNEXPLAINED` / `UNCERTAIN`.
- **Instant context, not just noise** — every alert ships with an AI summary and cited
  news sources, so an investor knows _why_ their stock moved within seconds.
- **Follow-up re-check** — `UNEXPLAINED` anomalies are re-evaluated after a short window;
  if late-breaking news arrives, a follow-up update is sent (verdict flip / confirm).
- **Provider-agnostic LLM** — switch between OpenAI, Gemini, or Claude by changing one
  env var (`LLM_MODEL`), no code change.
- **User-defined custom alerts** — users set their own thresholds via Telegram commands
  (`/setalert AAPL price > 200`).
- **Decoupled & fail-open** — the LLM layer never blocks delivery; on timeout/error the
  alert is forwarded as `UNCERTAIN`. Turn AI on/off by flipping `DELIVERY_SOURCE`.
- **Immutable lakehouse** — Apache Iceberg (Bronze/Silver/Gold) on MinIO, queryable via Trino.

---

## Tech Stack

| Layer          | Technology                                        | Role                                                                |
| -------------- | ------------------------------------------------- | ------------------------------------------------------------------- |
| Streaming      | Apache Kafka (Redpanda for local dev)             | Event backbone — quotes, trades, news, alerts                       |
| Microservices  | FastStream + FastAPI (async Python 3.12)          | Rule engine, LLM agent, alert service, producers, bot               |
| LLM            | LangGraph + `init_chat_model` (provider-agnostic) | News retrieval + anomaly classification                             |
| Batch / Stream | Apache Spark (Scala)                              | Daily rolling stats, tick aggregation, OLTP→Iceberg sync            |
| Lakehouse      | Apache Iceberg + MinIO (S3)                       | Immutable Bronze / Silver / Gold data lake                          |
| Catalog / Auth | Apache Gravitino + Keycloak (OAuth2)              | Iceberg REST catalog with token-based auth                          |
| Query          | Trino                                             | SQL analytics on Iceberg                                            |
| OLTP           | PostgreSQL 15                                     | `users`, `user_alert_rules`, `user_alert_events`, `sync_watermarks` |
| Orchestration  | Apache Airflow                                    | Scheduled Spark batch DAGs                                          |
| Runtime        | Kubernetes                                        | All services + infra run as deployments                             |
| Alerting       | Telegram Bot API                                  | System + custom alert delivery, bot commands                        |
| Data sources   | yfinance, Finnhub, NewsAPI.org                    | Market data and news                                                |

---

## Repository Layout

```
.
├── services/                      # Async Python microservices (one package each)
│   ├── rule-engine/               # Layer 0 — 6 statistical rules + custom-rule evaluator
│   ├── llm-agent/                 # Layer 1 — LangGraph news-validation agent
│   ├── alert-service/             # Sole Telegram sender; DLQ; Iceberg history + judgement
│   ├── telegram-bot/              # Bot commands (/setalert, /listalerts, ...)
│   ├── yfinance-quotes-producer/  # yfinance → raw.stock.quotes
│   ├── finnhub-trades-producer/   # Finnhub WS → raw.stock.trades
│   ├── finnhub-news-producer/     # Finnhub news → raw.stock.news
│   ├── db/                        # PostgreSQL migrations
│   ├── shared/                    # Shared Python utilities
│   ├── k8s/                       # Per-service k8s manifests
│   └── scripts/                   # build_and_push / run / stop helpers per service
│
├── spark-application/             # Apache Spark batch & streaming jobs (Scala)
│   ├── ohlcv-daily-loader/        # yfinance OHLCV → bronze
│   ├── news-ingest-stream/        # Finnhub news → bronze (streaming)
│   ├── news-cleaner/              # bronze → silver.normalized.news_clean
│   ├── rule-engine-context-builder/  # 20d rolling stats → gold.rule_engine_context
│   ├── fact-ohlcv-daily-builder/  # gold star-schema fact builder
│   ├── sync-custom-alerts/        # PostgreSQL → gold.fact_alert_history bridge
│   └── ...                        # dim-loader, trades-ohlcv-stream, company-info-loader, etc.
│
├── airflow-dags/                  # Airflow DAGs orchestrating the Spark batch pipeline
├── infra/k8s/                     # Cluster infra: storage, compute, ingestion, orchestration
├── docs/                          # Design plans, data lineage, deployment guide, thesis
└── CLAUDE.md                      # Engineering conventions & critical invariants
```

---

## Detection Pipeline

### Layer 0 — Rule Engine (real-time)

Consumes `raw.stock.quotes`, loads `gold.rule_engine_context` (20-day baselines) at startup,
and applies **6 rules** per quote (<10 ms target latency):

| Rule               | Trigger                   | HIGH severity |
| ------------------ | ------------------------- | ------------- |
| Price Z-Score      | `\|z_price\| > 3.0`       | `\|z\| > 4.5` |
| Volume Z-Score     | `z_vol > 3.0`             | `z > 5.0`     |
| Volume Ratio       | `vol / avg_vol_20d > 3.5` | —             |
| Bollinger Breakout | `bb_pos > 1.0` or `< 0.0` | —             |
| RSI Extreme        | `RSI > 80` or `< 20`      | —             |
| Intraday Range     | `(high − low) / low > 5%` | —             |

System anomalies → `alerts.raw`. Custom user rules are evaluated in the same path → `alerts.user`.

### Layer 1 — LLM Agent (real-time, LangGraph)

Consumes `alerts.raw`. Graph: `ingest → retrieve_news → classify → route`.

- **retrieve_news** unions two Iceberg catalogs — fresh tail (`bronze.raw.raw_news_articles`)
  and historical body (`silver.normalized.news_clean`) — then dedups to top-K.
- **classify** asks the LLM for a verdict + category + summary, applying a _relevance gate_:
  only news titles actually retrieved may be cited (anti-hallucination).
- **route** publishes a `ConfirmedAlertEvent` to `alerts.confirmed`. `UNEXPLAINED` alerts
  schedule a single re-check that may emit a `FollowUpEvent` on `alerts.followup`.
- **Safety:** TTL fail-open → `UNCERTAIN`, circuit breaker on repeated LLM failures, and
  `alert_id`-based idempotency.

`DELIVERY_SOURCE=raw` (default) keeps the system on the legacy path; flipping it to
`confirmed` activates the AI block and is reversible at any time.

---

## Data Lakehouse Layers

| Layer      | Examples                                                                                                                                              | Notes                       |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| **Bronze** | `raw.raw_ohlcv_daily`, `raw.raw_news_articles`, `raw.raw_company_info`                                                                                | Raw ingested data (Iceberg) |
| **Silver** | `normalized.ohlcv_daily`, `normalized.ohlcv_1min`, `normalized.news_clean`                                                                            | Cleaned & deduped           |
| **Gold**   | Star schema: `dim_symbol` (SCD2), `dim_date`, `fact_ohlcv_daily`, `fact_alert_history`; operational `rule_engine_context`; opt-in `anomaly_judgement` | Analytics-ready             |

Real-time quotes/trades stay **Kafka-only** (7-day retention — no time-series DB).
Full DDL: [`docs/`](docs/) and the gold-layer schema.

---

## Kafka Topics

| Topic              | Producer            | Consumer            | Payload             |
| ------------------ | ------------------- | ------------------- | ------------------- |
| `raw.stock.quotes` | yfinance producer   | rule-engine         | QuoteEvent          |
| `raw.stock.trades` | finnhub trades      | Spark (tick aggr.)  | TradeEvent          |
| `raw.stock.news`   | finnhub news        | Spark (news ingest) | NewsEvent           |
| `alerts.raw`       | rule-engine         | llm-agent           | AlertEvent          |
| `alerts.user`      | rule-engine         | alert-service       | CustomAlertEvent    |
| `alerts.confirmed` | llm-agent           | alert-service       | ConfirmedAlertEvent |
| `alerts.followup`  | llm-agent           | alert-service       | FollowUpEvent       |
| `alerts.failed`    | alert-service (DLQ) | operator tooling    | FailedAlertEnvelope |

> The Pydantic model in each service's `schema.py` is the single source of truth for a
> topic's JSON shape; corresponding Spark `StructType`s mirror it exactly.

---

## Custom Alerts

Users define their own thresholds via Telegram — no new service is added; the logic lives
inside the rule-engine and telegram-bot. PostgreSQL is the source of truth, Iceberg the
analytics sink.

**Commands:** `/setalert <SYMBOL|*> <field> <op> <threshold> [once|every]` · `/listalerts` ·
`/pausealert` · `/resumealert` · `/resetalert` · `/delalert` · `/alerthistory [SYMBOL]`

**Fields:** `price`, `daily_return`, `day_volume`, `volume_zscore`, `volume_ratio_20d`,
`price_zscore`, `rsi_14`, `bb_position` · **Operators:** `>` `<` `>=` `<=` `CROSSES_UP` `CROSSES_DOWN`

On `/setalert`: row inserted into PostgreSQL, then the bot calls the rule-engine's
`POST /internal/reload-user-rules` for a hot reload.

---

## Deployment (Kubernetes)

Each service ships with a `Dockerfile`, k8s manifests under `services/k8s/<svc>/`, and
helper scripts under `services/scripts/`:

```bash
# Build & push an image
bash services/scripts/build_and_push-llm-agent.sh v0.5

# Create the required secret (example: llm-agent with OpenAI)
kubectl create secret generic llm-agent-secret -n stock-anomaly-detection \
  --from-literal=OPENAI_API_KEY="sk-..." \
  --from-literal=ICEBERG_OAUTH2_CREDENTIAL="<client_id>:<client_secret>" \
  --from-literal=S3_ACCESS_KEY_ID="..." \
  --from-literal=S3_SECRET_ACCESS_KEY="..."

# Create Kafka topics
bash infra/k8s/orchestration/scripts/create_kafka_topics_plaintext.sh

# Deploy (rollout-status gated)
bash services/scripts/run-llm-agent.sh
bash services/scripts/run-alert-service.sh
```

**Turning the AI layer on** (final, reversible step): set `DELIVERY_SOURCE=confirmed` and
`KAFKA_INPUT_TOPIC=alerts.confirmed` on the alert-service. Roll back instantly by reverting
both. The Telegram bot's inbound webhook requires a public HTTPS tunnel (ngrok / Cloudflare
Tunnel) — see [`docs/deployment-guide.md`](docs/deployment-guide.md). Outbound alert
delivery needs **no** tunnel.

---

## Testing

Each Python service uses **pytest** with mocked Kafka/Iceberg/LLM (no live deps required):

```bash
cd services/<service>
pip install -e ".[test]"
python -m pytest tests/ -v
python -m pytest tests/ --cov=src --cov-report=term-missing   # coverage
```

Test types: unit (all 6 rules, custom-alert operators, LLM routing, fail-open, relevance gate,
circuit breaker, follow-up flip/confirm), **contract** tests (`ConfirmedAlertEvent` /
`FollowUpEvent` ↔ alert-service), and **provider** tests (≥2 LLM providers via `LLM_MODEL`).
Target coverage ≥ 80%.

---

## Documentation

| Document                                                                                                                           | Purpose                                       |
| ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| [`CLAUDE.md`](CLAUDE.md)                                                                                                           | Engineering conventions & critical invariants |
| [`docs/Finance_Anomaly_Detection_Platform_Plan_V3.3.md`](docs/Finance_Anomaly_Detection_Platform_Plan_V3.3.md)                     | Full system design                            |
| [`docs/ai-agent-plan.md`](docs/ai-agent-plan.md)                                                                                   | LLM news-validation agent architecture        |
| [`docs/data-lineage.md`](docs/data-lineage.md)                                                                                     | End-to-end data lineage                       |
| [`docs/deployment-guide.md`](docs/deployment-guide.md)                                                                             | Step-by-step deployment                       |
| [`docs/Sub-Plan_User-Defined_Custom_Alert-Final_Complete_Plan.md`](docs/Sub-Plan_User-Defined_Custom_Alert-Final_Complete_Plan.md) | Custom alert design                           |
| [`spark-application/README.md`](spark-application/README.md)                                                                       | Spark jobs overview                           |

---

> Built as a real-time data-engineering + applied-LLM platform demonstrating streaming
> detection, lakehouse analytics, and decoupled AI enrichment.
