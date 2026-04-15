# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project Overview

**Stock Anomaly Detection Platform V3.3** — Production-grade real-time financial anomaly detection that:

- Scans 500 US stocks in real-time for unusual volume/price anomalies
- Validates each anomaly with a 2-layer pipeline (rule-based → LLM)
- Delivers Telegram alerts with news context (explained vs unexplained)
- Supports user-defined custom alert rules via Telegram commands

**Target users:** Swing traders, portfolio managers, risk analysts, market researchers

---

## Tech Stack

| Layer                | Technology                        | Role                                                                                               |
| -------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------- |
| Streaming            | Kafka / Redpanda                  | Topics: `raw.stock.quotes`, `raw.stock.trades`, `raw.stock.news`, `alerts.raw`, `alerts.confirmed` |
| Microservices        | FastStream (async Python)         | Rule Engine, LLM Agent, Alert Service, data producers                                              |
| LLM orchestration    | LangGraph + Gemini 2.5 Flash-Lite | Parallel news research + data crosscheck workflow                                                  |
| Batch processing     | Apache Spark                      | Daily rolling stats, tick aggregation, OLTP→Iceberg sync                                           |
| Analytics storage    | Apache Iceberg + MinIO            | Immutable data lake (S3-compatible), Bronze/Silver/Gold layers                                     |
| Query engine         | Trino                             | SQL on Iceberg for dashboards and analytics                                                        |
| OLTP (custom alerts) | PostgreSQL 15                     | `users`, `user_alert_rules`, `user_alert_events`, `sync_watermarks`                                |
| Alerting             | Telegram Bot API                  | System anomaly alerts + custom alert delivery + bot commands                                       |
| Data sources         | yfinance, Finnhub, NewsAPI.org    | Market data and news feed                                                                          |

---

## Detection Architecture (2-Layer)

### Layer 0 — Rule Engine (FastStream, real-time)

Consumes `raw.stock.quotes`. Applies 6 rules using rolling stats pre-loaded from `gold.rule_engine_context` at startup:

| Rule               | Trigger                   | HIGH severity |
| ------------------ | ------------------------- | ------------- | ------ | --- | --- | ------ |
| Price Z-Score      | `                         | z_price       | > 3.0` | `   | z   | > 4.5` |
| Volume Z-Score     | `z_vol > 3.0`             | `z > 5.0`     |
| Volume Ratio       | `vol / avg_vol_20d > 3.5` | —             |
| Bollinger Breakout | `bb_pos > 1.0` or `< 0.0` | —             |
| RSI Extreme        | `RSI > 80` or `< 20`      | —             |
| Intraday Range     | `(high−low)/low > 5%`     | —             |

Anomalies that pass thresholds → published to Kafka `alerts.raw`.

The Rule Engine **also evaluates user-defined custom alert rules** loaded from PostgreSQL into memory. On match: sends Telegram directly, writes to `user_alert_events` (PostgreSQL), updates rule status.

### Layer 1 — LLM Agent (LangGraph, real-time)

Consumes `alerts.raw`. Parallel graph:

```
data_conversion → [news_research ‖ data_crosscheck] → aggregation → routing
```

- **news_research**: Query NewsAPI for last 6h of articles → Gemini judges: EXPLAINED / UNEXPLAINED / UNCERTAIN
- **data_crosscheck**: Compare Finnhub price vs yfinance — discrepancy >10% → DATA_ERROR

Routing output:

- `NEWS_EXPLAINED` → log only (save `news_category`, no alert)
- `UNEXPLAINED` → publish to `alerts.confirmed`
- `DATA_ERROR` → discard

LLM Agent Service consumes `alerts.confirmed` → formats Telegram message → sends → logs to `fact_alert_history`.

---

## Data Flow (end-to-end)

```
yfinance WS ──────────────────────► Kafka: raw.stock.quotes
                                            │
                                            ▼
Finnhub WS ────────────────────────► Kafka: raw.stock.trades
                                            │ Spark Structured Streaming
                                            ▼
NewsAPI (5min poll) ─► Kafka: raw.stock.news ─► bronze.raw_news_articles (Iceberg)
                                            │
                                            │ Spark batch (daily)
                                            ▼
                               silver.ohlcv_daily / silver.ohlcv_1min
                                            │
                                            │ Spark batch (07:00 UTC)
                                            ▼
                               gold.rule_engine_context (pre-loaded to memory)

raw.stock.quotes ──► [Rule Engine] ──► Kafka: alerts.raw
                            │
                     Custom alert eval ──► PostgreSQL + Telegram
                            │
                    [LLM Agent] ──► Kafka: alerts.confirmed
                            │
                    [Alert Service] ──► Telegram + fact_alert_history (Iceberg)
```

**Key principle:** Real-time data (quotes, trades) lives **only in Kafka** (7-day retention). No TimescaleDB. Only historical/analytical data goes to Iceberg.

---

## Data Layers

### Bronze (Iceberg + Kafka-only)

| Data                       | Source             | Storage                                      |
| -------------------------- | ------------------ | -------------------------------------------- |
| Daily OHLCV (20yr history) | yfinance batch     | `bronze.raw_ohlcv_daily` (Iceberg)           |
| Company metadata           | yfinance `.info`   | `bronze.raw_company_info` (Iceberg)          |
| Real-time quotes           | yfinance WebSocket | Kafka `raw.stock.quotes` only                |
| Trade ticks                | Finnhub WebSocket  | Kafka `raw.stock.trades` only                |
| News articles              | NewsAPI REST       | Kafka → `bronze.raw_news_articles` (Iceberg) |

### Silver (Iceberg — cleaned, normalized)

- `silver.ohlcv_daily` — split-adjusted OHLCV
- `silver.ohlcv_1min` — 1-min bars aggregated from Finnhub ticks
- `silver.news_clean` — deduplicated news articles

### Gold — Star Schema (Iceberg)

**Dimensions:** `dim_symbol` (SCD2), `dim_date`, `dim_time`, `dim_anomaly_type`, `dim_rule`, `dim_news_category`

**Facts:**

- `fact_ohlcv_daily` — grain: symbol×day; includes price/volume + rolling stats + RSI, MACD, BB, ATR
- `fact_anomaly_daily` — grain: symbol×event; includes triggered rules, severity, LLM judgment, news_category
- `fact_alert_history` — grain: symbol×alert; includes `alert_source` column (`'system'` or `'user_custom'`)

**Operational context (not Star Schema):**

- `gold.rule_engine_context` — pre-computed 20d rolling stats (mean, std, BB, RSI, ATR); updated daily 07:00 UTC; pre-loaded into Rule Engine memory

See `docs/gold_layer_schema.sql` for full DDL.

---

## Custom Alert Feature (PostgreSQL OLTP)

User-defined alerts managed entirely in PostgreSQL. **Do not add any new service** — this extends the existing Rule Engine and Telegram Bot.

### PostgreSQL Tables

```
users              — telegram_id → user_id (UUID)
user_alert_rules   — rule_id, user_id, symbols[], field, operator, threshold,
                     frequency (ONCE|EVERY_TIME), cooldown_min, status (ACTIVE|PAUSED|TRIGGERED)
user_alert_events  — immutable event log; snapshots field/operator/threshold at fire time
sync_watermarks    — job_name, last_sync_at (for incremental Iceberg sync)
```

### Supported Alert Fields

`price`, `daily_return`, `day_volume`, `volume_zscore`, `volume_ratio_20d`, `price_zscore`, `rsi_14`, `bb_position`

Operators: `>`, `<`, `>=`, `<=`, `CROSSES_UP`, `CROSSES_DOWN`

> **Note:** `rsi_14` and `bb_position` are from `rule_engine_context` (daily batch), not real-time intraday. Alert messages must state this.

### Telegram Bot Commands

```
/setalert <SYMBOL|*> <field> <operator> <threshold> [once|every]
/listalerts
/pausealert <rule_id>
/resumealert <rule_id>
/resetalert <rule_id>
/delalert <rule_id>
/alerthistory [SYMBOL]
```

On `/setalert`: INSERT into PostgreSQL → POST `/internal/reload-user-rules` to Rule Engine (hot-reload without restart).

### OLTP–OLAP Bridge

PostgreSQL is **source of truth** for custom alert runtime. Iceberg is **analytics sink** (read-only copy for dashboards).

Daily Spark job `sync_custom_alerts` (07:30 UTC):

1. Read `last_sync_at` from `sync_watermarks`
2. Query `user_alert_events WHERE triggered_at > last_sync_at` (incremental by watermark)
3. Map → append to `gold.fact_alert_history` with `alert_source = 'user_custom'`
4. Update `sync_watermarks` on success

Bridge contract documented in `docs/oltp-olap-bridge.md`.

---

## Daily Batch Schedule (UTC)

| Time  | Job                               | Output                                  |
| ----- | --------------------------------- | --------------------------------------- |
| 06:00 | yfinance OHLCV loader             | `bronze.raw_ohlcv_daily`                |
| 07:00 | Spark `build_rule_context`        | `gold.rule_engine_context`              |
| 07:15 | Rule Engine: reload context_cache | in-memory refresh                       |
| 07:30 | Spark `sync_custom_alerts`        | `gold.fact_alert_history` (custom rows) |

---

## Service Components

### 1. Rule Engine (FastStream)

- Consumes: `raw.stock.quotes`
- Startup: load `gold.rule_engine_context` + `user_alert_rules` (PostgreSQL) into memory
- Applies system rules → publishes to `alerts.raw`
- Evaluates user custom rules → Telegram + PostgreSQL writes
- Exposes: `POST /internal/reload-user-rules` (called by Telegram Bot on rule change)

### 2. LLM Agent Service (FastStream + LangGraph)

- Consumes: `alerts.raw`
- LangGraph: `data_conversion → [news_research ‖ data_crosscheck] → aggregation → routing`
- Publishes: `alerts.confirmed`

### 3. Alert Service (FastStream)

- Consumes: `alerts.confirmed`
- Formats Telegram message → sends → logs to `fact_alert_history`

### 4. Telegram Bot

- Handles `/setalert` and other commands
- Reads/writes PostgreSQL directly
- Calls Rule Engine `/internal/reload-user-rules` on rule changes

### 5. Spark Batch Jobs

- `build_rule_context` — 07:00 UTC, rolling stats → `rule_engine_context`
- `sync_custom_alerts` — 07:30 UTC, PostgreSQL → Iceberg (watermark-based)
- Finnhub tick aggregator — Spark Structured Streaming, continuous, → `silver.ohlcv_1min`
- NewsAPI Spark writer — micro-batch → `bronze.raw_news_articles`

### 6. Data Ingestion Producers

- yfinance daily loader → `bronze.raw_ohlcv_daily` + `raw.stock.quotes` (Kafka)
- Finnhub WebSocket → `raw.stock.trades` (Kafka)
- NewsAPI poller → `raw.stock.news` (Kafka)

---

## Key Files & Docs

| File                                                                              | Purpose                                            |
| --------------------------------------------------------------------------------- | -------------------------------------------------- |
| `docs/Finance Anomaly Detection Platform – Plan V3.3 Final (Rule-Based + LLM).md` | Full system design                                 |
| `docs/Sub-Plan  User-Defined Custom Alert — Final Complete Plan.md`               | Custom alert feature plan                          |
| `docs/innovation-complete.md`                                                     | Watermark sync + OLTP-OLAP bridge contract details |
| `docs/gold_layer_schema.sql`                                                      | Star schema DDL                                    |
| `docs/gold_layer_schema.dbml`                                                     | ER diagram                                         |
| `docs/Banchelor_Thesis.pdf`                                                       | Thesis background                                  |

---

## Coding Conventions

- **Language:** Python (FastStream, LangGraph, pydantic, SQLAlchemy async)
- **Immutability:** Always return new objects; never mutate state in-place
- **Async everywhere:** FastStream is async — all handlers and DB clients must be `async def`
- **Structured output:** LLM responses use Pydantic models for judgment + category fields
- **No magic values:** Rule thresholds, cooldown defaults, and Kafka topic names go in config/constants
- **PostgreSQL ENUMs:** `alert_field`, `alert_operator`, `alert_status`, `alert_frequency` — use these; never raw strings
- **Error handling:** Explicit at every layer; never swallow exceptions silently
- **Context cache:** `context_cache: dict[str, dict]` keyed by symbol — treat as read-only within a quote handler; refresh only at startup or daily reload
- **Custom rule evaluator:** `get_field_value()` + `evaluate_condition()` — pure functions, unit-testable without Kafka

## Local Dev Setup

1. Docker Compose: Redpanda (Kafka), MinIO, PostgreSQL
2. Iceberg catalog: SQLite-based for local dev
3. `.env`: `GEMINI_API_KEY`, `NEWSAPI_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `PG_*`
4. Run Rule Engine and producers separately; use Redpanda Console to inspect topics

## Testing Strategy

- **Unit:** Rule logic (all 6 rules), custom alert evaluator (all operators including CROSSES\_\*), LLM prompt templates
- **Integration:** Quote → anomaly → Kafka `alerts.raw`; custom rule fire → PostgreSQL event inserted
- **Data quality:** `fact_anomaly_daily` grain validation, watermark sync correctness
- **Load:** 500 symbols throughput, Rule Engine latency <10ms per quote

<!-- dgc-policy-v11 -->
# Dual-Graph Context Policy

This project uses a local dual-graph MCP server for efficient context retrieval.

## MANDATORY: Always follow this order

1. **Call `graph_continue` first** — before any file exploration, grep, or code reading.

2. **If `graph_continue` returns `needs_project=true`**: call `graph_scan` with the
   current project directory (`pwd`). Do NOT ask the user.

3. **If `graph_continue` returns `skip=true`**: project has fewer than 5 files.
   Do NOT do broad or recursive exploration. Read only specific files if their names
   are mentioned, or ask the user what to work on.

4. **Read `recommended_files`** using `graph_read` — **one call per file**.
   - `graph_read` accepts a single `file` parameter (string). Call it separately for each
     recommended file. Do NOT pass an array or batch multiple files into one call.
   - `recommended_files` may contain `file::symbol` entries (e.g. `src/auth.ts::handleLogin`).
     Pass them verbatim to `graph_read(file: "src/auth.ts::handleLogin")` — it reads only
     that symbol's lines, not the full file.
   - Example: if `recommended_files` is `["src/auth.ts::handleLogin", "src/db.ts"]`,
     call `graph_read(file: "src/auth.ts::handleLogin")` and `graph_read(file: "src/db.ts")`
     as two separate calls (they can be parallel).

5. **Check `confidence` and obey the caps strictly:**
   - `confidence=high` -> Stop. Do NOT grep or explore further.
   - `confidence=medium` -> If recommended files are insufficient, call `fallback_rg`
     at most `max_supplementary_greps` time(s) with specific terms, then `graph_read`
     at most `max_supplementary_files` additional file(s). Then stop.
   - `confidence=low` -> Call `fallback_rg` at most `max_supplementary_greps` time(s),
     then `graph_read` at most `max_supplementary_files` file(s). Then stop.

## Token Usage

A `token-counter` MCP is available for tracking live token usage.

- To check how many tokens a large file or text will cost **before** reading it:
  `count_tokens({text: "<content>"})`
- To log actual usage after a task completes (if the user asks):
  `log_usage({input_tokens: <est>, output_tokens: <est>, description: "<task>"})`
- To show the user their running session cost:
  `get_session_stats()`

Live dashboard URL is printed at startup next to "Token usage".

## Rules

- Do NOT use `rg`, `grep`, or bash file exploration before calling `graph_continue`.
- Do NOT do broad/recursive exploration at any confidence level.
- `max_supplementary_greps` and `max_supplementary_files` are hard caps - never exceed them.
- Do NOT dump full chat history.
- Do NOT call `graph_retrieve` more than once per turn.
- After edits, call `graph_register_edit` with the changed files. Use `file::symbol` notation (e.g. `src/auth.ts::handleLogin`) when the edit targets a specific function, class, or hook.

## Context Store

Whenever you make a decision, identify a task, note a next step, fact, or blocker during a conversation, call `graph_add_memory`.

**To add an entry:**
```
graph_add_memory(type="decision|task|next|fact|blocker", content="one sentence max 15 words", tags=["topic"], files=["relevant/file.ts"])
```

**Do NOT write context-store.json directly** — always use `graph_add_memory`. It applies pruning and keeps the store healthy.

**Rules:**
- Only log things worth remembering across sessions (not every minor detail)
- `content` must be under 15 words
- `files` lists the files this decision/task relates to (can be empty)
- Log immediately when the item arises — not at session end

## Session End

When the user signals they are done (e.g. "bye", "done", "wrap up", "end session"), proactively update `CONTEXT.md` in the project root with:
- **Current Task**: one sentence on what was being worked on
- **Key Decisions**: bullet list, max 3 items
- **Next Steps**: bullet list, max 3 items

Keep `CONTEXT.md` under 20 lines total. Do NOT summarize the full conversation — only what's needed to resume next session.
