# Remaining Spark Jobs — Bronze / Silver / Gold

> **Already implemented:** `news-ingest-stream` (Bronze), `ohlcv-daily-loader` (Bronze), `trades-ohlcv-stream` (Silver), `company-info-loader` (Bronze), `ohlcv-daily-cleaner` (Silver), `news-cleaner` (Silver), `dim-loader` (Gold), `fact-ohlcv-daily-builder` (Gold).
> Everything below is **not yet built**.

---

## Gold Layer

### `rule-engine-context-builder`

**Purpose:** Daily batch (07:00 UTC) that extracts the **latest** rolling stats snapshot from `gold.fact_ohlcv_daily` into `gold.rule_engine_context` — the in-memory preload table for the Rule Engine service.

- **Schedule:** Daily 07:00 UTC (after `fact-ohlcv-daily-builder`)
- **Output table:** `gravitino_catalog.gold.rule_engine_context`
- **Key logic:**
  - Filter `fact_ohlcv_daily` to `trade_date = yesterday` (most recent completed trading day)
  - Select: `symbol`, `as_of_date`, `mean_return_20d`, `std_return_20d`, `mean_return_5d`, `std_return_5d`, `mean_volume_20d`, `std_volume_20d`, `mean_volume_5d`, `bb_upper_20d`, `bb_lower_20d`, `bb_mid_20d`, `atr_14`, `rsi_14`, `vwap_5d_avg`
  - Overwrite partition for `as_of_date = yesterday` (idempotent)
  - Rule Engine service reloads this table at 07:15 UTC via its `/internal/reload-context` endpoint

### `sync-custom-alerts`

**Purpose:** Daily batch (07:30 UTC) that syncs new `user_alert_events` rows from PostgreSQL OLTP into `gold.fact_alert_history` in Iceberg, using watermark-based incremental sync.

- **Schedule:** Daily 07:30 UTC (independent of the OHLCV pipeline)
- **Output table:** `gravitino_catalog.gold.fact_alert_history` (rows with `alert_source = 'user_custom'`)
- **Key logic:**
  1. Read `last_sync_at` from PostgreSQL `sync_watermarks` WHERE `job_name = 'custom_alerts_to_iceberg'`
  2. Query `user_alert_events WHERE triggered_at > last_sync_at` (incremental)
  3. Map columns per OLTP–OLAP bridge contract (see `docs/oltp-olap-bridge.md`): `event_id → alert_id`, `triggered_at → alerted_at`, `delivered → delivery_status`, hardcode `alert_source = 'user_custom'`
  4. Append rows to `gold.fact_alert_history`
  5. On success: `UPDATE sync_watermarks SET last_sync_at = NOW() WHERE job_name = 'custom_alerts_to_iceberg'`
- **Note:** Requires PostgreSQL JDBC driver in the fat JAR (`org.postgresql:postgresql`). Uses JDBC reads (`spark.read.jdbc`) and a direct JDBC update for watermark commit.

---

## Summary Table

| Job                           | Layer  | Schedule          | Depends on                     | Status   |
| ----------------------------- | ------ | ----------------- | ------------------------------ | -------- |
| `company-info-loader`         | Bronze | Weekly Sun 05:00  | —                              | **DONE** |
| `ohlcv-daily-cleaner`         | Silver | Daily 06:30       | `ohlcv-daily-loader`           | **DONE** |
| `news-cleaner`                | Silver | Daily 06:00       | `news-ingest-stream`           | **DONE** |
| `dim-loader`                  | Gold   | Weekly + one-time | `company-info-loader`          | **DONE** |
| `fact-ohlcv-daily-builder`    | Gold   | Daily 07:00       | `ohlcv-daily-cleaner`          | **DONE** |
| `rule-engine-context-builder` | Gold   | Daily 07:15       | `fact-ohlcv-daily-builder`     | **TODO** |
| `sync-custom-alerts`          | Gold   | Daily 07:30       | PostgreSQL `user_alert_events` | **TODO** |

### Daily pipeline order (UTC)

```
06:00  ohlcv-daily-loader        (Bronze — yfinance OHLCV)
06:00  news-cleaner               (Silver — runs concurrently)
06:30  ohlcv-daily-cleaner        (Silver — waits for ohlcv-daily-loader)
07:00  fact-ohlcv-daily-builder   (Gold — waits for ohlcv-daily-cleaner)
07:15  rule-engine-context-builder (Gold — waits for fact-ohlcv-daily-builder)
07:30  sync-custom-alerts          (Gold — independent, watermark-driven)

Weekly (Sun 05:00):
  company-info-loader  →  dim-loader
```
