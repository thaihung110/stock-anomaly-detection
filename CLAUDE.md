# CLAUDE.md

## Project Overview

**Stock Anomaly Detection Platform V3.3** — Real-time financial anomaly detection for 500 US stocks. 2-layer pipeline: rule-based → LLM validation. Delivers Telegram alerts with news context. Supports user-defined custom alerts via Telegram commands.

---

## Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| Streaming | Kafka / Redpanda | Topics: `raw.stock.quotes`, `raw.stock.trades`, `raw.stock.news`, `alerts.raw`, `alerts.confirmed` |
| Microservices | FastStream (async Python) | Rule Engine, LLM Agent, Alert Service, data producers |
| LLM | LangGraph + Gemini 2.5 Flash-Lite | Parallel news research + data crosscheck |
| Batch | Apache Spark (Scala) | Daily rolling stats, tick aggregation, OLTP→Iceberg sync |
| Analytics | Apache Iceberg + MinIO | Immutable data lake — Bronze/Silver/Gold layers |
| Query | Trino | SQL on Iceberg for dashboards |
| OLTP | PostgreSQL 15 | `users`, `user_alert_rules`, `user_alert_events`, `sync_watermarks` |
| Alerting | Telegram Bot API | System alerts + custom alert delivery + bot commands |
| Data sources | yfinance, Finnhub, NewsAPI.org | Market data and news |

---

## Detection Architecture

### Layer 0 — Rule Engine (real-time, FastStream)

Consumes `raw.stock.quotes`. Loads `gold.rule_engine_context` at startup. Applies 6 rules:

| Rule | Trigger | HIGH severity |
|------|---------|--------------|
| Price Z-Score | `\|z_price\| > 3.0` | `\|z\| > 4.5` |
| Volume Z-Score | `z_vol > 3.0` | `z > 5.0` |
| Volume Ratio | `vol / avg_vol_20d > 3.5` | — |
| Bollinger Breakout | `bb_pos > 1.0` or `< 0.0` | — |
| RSI Extreme | `RSI > 80` or `< 20` | — |
| Intraday Range | `(high−low)/low > 5%` | — |

Anomalies → `alerts.raw`. Also evaluates user custom rules → Telegram + PostgreSQL.

### Layer 1 — LLM Agent (real-time, LangGraph)

Consumes `alerts.raw`. Graph: `data_conversion → [news_research ‖ data_crosscheck] → aggregation → routing`

- `NEWS_EXPLAINED` → log only; `UNEXPLAINED` → `alerts.confirmed`; `DATA_ERROR` → discard

---

## Data Layers

**Bronze:** `bronze.raw_ohlcv_daily`, `bronze.raw_company_info`, `bronze.raw_news_articles` (Iceberg). Real-time quotes/trades stay **Kafka-only** (7-day retention — no TimescaleDB).

**Silver:** `silver.ohlcv_daily`, `silver.ohlcv_1min`, `silver.news_clean`

**Gold (Star Schema):**
- Dims: `dim_symbol` (SCD2), `dim_date`, `dim_time`, `dim_anomaly_type`, `dim_rule`, `dim_news_category`
- Facts: `fact_ohlcv_daily`, `fact_anomaly_daily`, `fact_alert_history` (`alert_source`: `'system'` or `'user_custom'`)
- Operational: `gold.rule_engine_context` — 20d rolling stats (mean, std, BB, RSI, ATR); updated 07:00 UTC

Full DDL: `docs/gold_layer_schema.sql`

---

## Custom Alert Feature

**Do not add any new service.** Extends existing Rule Engine + Telegram Bot. PostgreSQL is source of truth; Iceberg is analytics sink.

### PostgreSQL Tables
```
users              — telegram_id → user_id (UUID)
user_alert_rules   — rule_id, user_id, symbols[], field, operator, threshold,
                     frequency (ONCE|EVERY_TIME), cooldown_min, status (ACTIVE|PAUSED|TRIGGERED)
user_alert_events  — immutable event log; snapshots field/operator/threshold at fire time
sync_watermarks    — job_name, last_sync_at
```

### Supported Fields & Operators
Fields: `price`, `daily_return`, `day_volume`, `volume_zscore`, `volume_ratio_20d`, `price_zscore`, `rsi_14`, `bb_position`
Operators: `>`, `<`, `>=`, `<=`, `CROSSES_UP`, `CROSSES_DOWN`

> `rsi_14` and `bb_position` are from daily batch (not real-time intraday) — alert messages must state this.

### Telegram Commands
`/setalert <SYMBOL|*> <field> <op> <threshold> [once|every]` · `/listalerts` · `/pausealert` · `/resumealert` · `/resetalert` · `/delalert` · `/alerthistory [SYMBOL]`

On `/setalert`: INSERT → PostgreSQL, then POST `/internal/reload-user-rules` to Rule Engine (hot-reload).

### OLTP–OLAP Bridge (Spark `sync_custom_alerts`, 07:30 UTC)
1. Read `last_sync_at` from `sync_watermarks`
2. Query `user_alert_events WHERE triggered_at > last_sync_at`
3. Append to `gold.fact_alert_history` with `alert_source = 'user_custom'`
4. Update `sync_watermarks` on success

---

## Daily Batch Schedule (UTC)

| Time | Job | Output |
|------|-----|--------|
| 06:00 | yfinance OHLCV loader | `bronze.raw_ohlcv_daily` |
| 07:00 | Spark `build_rule_context` | `gold.rule_engine_context` |
| 07:15 | Rule Engine reload | in-memory refresh |
| 07:30 | Spark `sync_custom_alerts` | `gold.fact_alert_history` (custom rows) |

---

## Services

1. **Rule Engine** — consumes `raw.stock.quotes`; loads context + user rules at startup; publishes `alerts.raw`; exposes `POST /internal/reload-user-rules`
2. **LLM Agent** — consumes `alerts.raw`; LangGraph pipeline; publishes `alerts.confirmed`
3. **Alert Service** — consumes `alerts.confirmed`; formats + sends Telegram; logs to `fact_alert_history`
4. **Telegram Bot** — handles commands; reads/writes PostgreSQL; calls Rule Engine on rule changes
5. **Spark Batch** — `build_rule_context`, `sync_custom_alerts`, Finnhub tick aggregator, NewsAPI writer
6. **Producers** — yfinance daily loader, Finnhub WebSocket → Kafka, NewsAPI poller

---

## Coding Conventions

- **Language:** Python (FastStream, LangGraph, Pydantic, SQLAlchemy async). **Spark jobs: Scala.**
- **Async everywhere:** all handlers and DB clients must be `async def`
- **No magic values:** thresholds, cooldowns, topic names → config/constants
- **PostgreSQL ENUMs:** `alert_field`, `alert_operator`, `alert_status`, `alert_frequency` — never raw strings
- **Immutability:** always return new objects; never mutate state in-place
- **Context cache:** `context_cache: dict[str, dict]` keyed by symbol — read-only within quote handler
- **Custom rule evaluator:** `get_field_value()` + `evaluate_condition()` — pure functions

## Local Dev Setup

1. Docker Compose: Redpanda, MinIO, PostgreSQL
2. Iceberg catalog: SQLite-based for local dev
3. `.env`: `GEMINI_API_KEY`, `NEWSAPI_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `PG_*`

## Testing

- **Unit:** all 6 rules, custom alert evaluator (all operators incl. CROSSES_*), LLM prompt templates
- **Integration:** quote → `alerts.raw`; custom rule fire → PostgreSQL event inserted
- **Load:** 500 symbols throughput, Rule Engine latency <10ms per quote

## Key Docs

| File | Purpose |
|------|---------|
| `docs/Finance Anomaly Detection Platform – Plan V3.3 Final (Rule-Based + LLM).md` | Full system design |
| `docs/Sub-Plan  User-Defined Custom Alert — Final Complete Plan.md` | Custom alert plan |
| `docs/innovation-complete.md` | Watermark sync + OLTP-OLAP bridge contract |
| `docs/gold_layer_schema.sql` | Star schema DDL |
