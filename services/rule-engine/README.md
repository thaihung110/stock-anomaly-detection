# rule-engine

Layer 0 of the detection pipeline: consumes real-time quotes, evaluates 6 built-in anomaly rules plus every user-defined custom alert rule, and publishes fired alerts. Owns the in-memory `gold.rule_engine_context` snapshot loaded from Iceberg at startup — this is what lets it evaluate 20-day rolling stats per quote without recomputing them.

## Architecture

```
raw.stock.quotes (Kafka)
    ↓ FastStream @router.subscriber
handle_quote()
    ├─→ RuleOrchestrator.evaluate()      (6 system rules, uses _context_cache)
    │       ↓ fired, not in cooldown
    │   alerts.raw (Kafka)
    └─→ UserAlertProcessor.evaluate()    (custom rules, uses _context_cache + PostgreSQL)
            ↓ fired, not in cooldown
        INSERT user_alert_events (PostgreSQL, durable BEFORE Kafka publish)
            ↓
        alerts.user (Kafka)
```

**No Telegram client in this service** (ADR-001) — it only publishes to Kafka; `alert-service` is the sole Telegram sender for both paths.

## Pipeline Steps

### Startup (`main.py` lifespan)

1. `load_context()` (`infrastructure/context_loader.py`) reads `gold.rule_engine_context` from the Iceberg REST catalog (Gravitino) into an in-memory `_context_cache: dict[symbol, dict[field, float]]`. For symbols with multiple `as_of_date` partitions in the table, only the **most recent** `as_of_date` per symbol is kept (Iceberg scan order is non-deterministic, so this dedup is required to avoid loading a stale baseline). `rsi_14` nulls are coerced to a neutral `50.0` (not `0.0` — a `0.0` RSI would falsely satisfy `rsi < 20` and fire an oversold alert on every quote for a symbol with insufficient history).
2. `DbClient` connects to PostgreSQL (`user_alert_rules`/`user_alert_events`/`users`).
3. `UserAlertProcessor.reload_rules()` loads all `ACTIVE` custom rules into memory.

### Per-quote handling (`handle_quote`)

For each `QuoteEvent` on `raw.stock.quotes`, looks up the symbol's context (skips silently if the symbol isn't in `_context_cache` — i.e. not covered by the daily batch), then runs both evaluators against the **same context snapshot**:

**`RuleOrchestrator.evaluate`** (`application/rule_orchestrator.py`) — runs all 6 rules in `domain/rules.py` (`ALL_RULES` tuple, order fixed) against the quote + context. Each rule is a pure function `(QuoteEvent, ctx, Settings) -> AlertEvent | None`:

| Rule | Formula | MEDIUM | HIGH |
|---|---|---|---|
| `rule_price_zscore` | `z = ((price-prev_close)/prev_close) / std_return_20d` | `|z| > 3.0` | `|z| > 4.5` |
| `rule_volume_zscore` | `z = (day_volume - mean_volume_20d) / std_volume_20d` | `z > 3.0` | `z > 5.0` |
| `rule_volume_ratio` | `day_volume / mean_volume_20d` | `> 3.5` | — |
| `rule_bollinger_breakout` | `(price - bb_lower) / (bb_upper - bb_lower)` | outside `[0, 1]` | — |
| `rule_rsi_extreme` | context `rsi_14` | `> 80` or `< 20` | — |
| `rule_intraday_range` | `(day_high - day_low) / day_low` | `> 5%` | — |

Every rule returns `None` (no alert) if a required denominator is zero (e.g. `std_return_20d == 0.0`) — guards against divide-by-zero on symbols with too little history rather than raising.

A **per-(symbol, rule) cooldown** (`system_alert_cooldown_min`, default 60 min) suppresses re-firing while the same condition stays true (e.g. RSI pinned above 80 all day) — check-and-record happens atomically under one `asyncio.Lock` so two concurrently processed quotes can't both slip past the check.

**`UserAlertProcessor.evaluate`** (`application/user_alert_processor.py`) — for every cached `ACTIVE` custom rule (excluded if `rule.symbols` doesn't include `*` or the quote's symbol):
1. `get_field_value()` (`domain/custom_rules.py`) computes the field's current value — either read directly off the quote (`price`, `daily_return`, `day_volume`) or derived from quote+context (`price_zscore`, `volume_zscore`, `volume_ratio_20d`, `bb_position` are computed fresh here, **not** looked up as context keys) or read straight from context (`rsi_14`).
2. `evaluate_condition()` checks the operator against `threshold`, using the field's previous value (tracked separately per `(symbol, field)`) for `CROSSES_UP`/`CROSSES_DOWN`.
3. On cooldown-pass: **`INSERT user_alert_events` first**, cooldown/publish only recorded **after** the insert succeeds — if the DB insert fails, the rule stays eligible to retry on the next quote instead of silently losing the fire. A Kafka publish failure is caught and logged but never re-raised (would otherwise trigger consumer redelivery and a double-fire).
4. `ONCE`-frequency rules are removed from the in-memory cache immediately after firing (and marked `TRIGGERED` in Postgres) so they don't fire again before the next `/internal/reload-user-rules` call.

After both evaluators run, `update_prev_values()` snapshots every field's current value per symbol — this must happen **after** evaluation so crossing-detection compares against the prior quote's value, not the one just processed.

## Kafka Contracts

| Topic | Direction | Schema | Partition Key |
|---|---|---|---|
| `raw.stock.quotes` | Consume | `QuoteEvent` | — |
| `alerts.raw` | Produce | `AlertEvent` (`alert_id`, `symbol`, `rule_name`, `severity`, `triggered_value`, `threshold`, `context_snapshot`) | default |
| `alerts.user` | Produce | `CustomAlertEvent` (`event_id`, `rule_id`, `user_id`, `chat_id`, `symbol`, `field`, `operator`, `threshold`, `triggered_value`, `triggered_at`) | default |

`CustomAlertEvent` carries everything `alert-service` needs to format + route the Telegram message without a DB round-trip — including `chat_id` joined from `users` at rule-fetch time (`null` → alert-service falls back to the admin chat).

## HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | `{"status": "ok", "symbols_loaded": "<count>"}` |
| `/internal/reload-user-rules` | POST | **Reloads both** the Iceberg context cache **and** the custom-rule cache from Postgres — called by Telegram Bot after any `/setalert`/`/pausealert`/`/delalert` mutation. If the context reload comes back empty, the **old cache is kept** (fails safe rather than wiping context to zero symbols) and `status: "error"` is returned. |

## Configuration

Env vars read by `Settings` (`config.py`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | No | `localhost:9092` | |
| `KAFKA_INPUT_TOPIC` | No | `raw.stock.quotes` | |
| `KAFKA_OUTPUT_TOPIC` | No | `alerts.raw` | |
| `KAFKA_USER_ALERT_TOPIC` | No | `alerts.user` | |
| `ICEBERG_CATALOG_URI` / `ICEBERG_OAUTH2_SERVER_URI` / `ICEBERG_OAUTH2_CREDENTIAL` / `ICEBERG_OAUTH2_SCOPE` | Yes (credential) | Gravitino/Keycloak defaults | |
| `ICEBERG_CATALOG_NAME` | No | `stock_catalog` | |
| `ICEBERG_WAREHOUSE` | No | `gold` | |
| `RULE_ENGINE_CONTEXT_TABLE` | No | `gold.rule_engine_context` | |
| `S3_ENDPOINT` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_REGION` / `S3_PATH_STYLE_ACCESS` | Yes (keys) | MinIO defaults | Direct object-store access for PyIceberg |
| `PRICE_ZSCORE_TRIGGER` / `_HIGH` | No | `3.0` / `4.5` | |
| `VOL_ZSCORE_TRIGGER` / `_HIGH` | No | `3.0` / `5.0` | |
| `VOL_RATIO_TRIGGER` | No | `3.5` | |
| `RSI_OVERBOUGHT` / `RSI_OVERSOLD` | No | `80.0` / `20.0` | |
| `INTRADAY_RANGE_TRIGGER` | No | `0.05` | |
| `PG_HOST` / `PG_PORT` / `PG_DATABASE` / `PG_USER` / `PG_PASSWORD` | Yes (password) | `localhost` / `5432` / `stock_anomaly` / `stock_user` / — | Assembled into `pg_dsn` if not set directly |
| `SYSTEM_ALERT_COOLDOWN_MIN` | No | `60` | Per-(symbol, rule) system-alert cooldown |
| `HTTP_PORT` | No | `8080` | |

## Catalog / Connection Config

Registers `gravitino_gold` (read-only — `gold.rule_engine_context` is written exclusively by the Spark `rule-engine-context-builder` job at 07:00 UTC; this service only loads it, per `CLAUDE.md`'s Data Layer Boundaries). PostgreSQL connection is a plain `asyncpg` pool for `user_alert_rules`/`user_alert_events`/`users`.

## Kubernetes Resource Sizing

From `k8s/rule-engine/deployment.yaml`:

- 1 replica · **Requests**: 100m CPU, 256Mi memory · **Limits**: 500m CPU, 512Mi memory
- Liveness/readiness on `/health` (initial delay 30s/15s, period 15s/10s)
- Secret `rule-engine-secret` required: `ICEBERG_OAUTH2_CREDENTIAL`, `PG_PASSWORD`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`

```bash
kubectl create secret generic rule-engine-secret \
  -n stock-anomaly-detection \
  --from-literal=ICEBERG_OAUTH2_CREDENTIAL="<client_id>:<client_secret>" \
  --from-literal=PG_PASSWORD="<password>" \
  --from-literal=S3_ACCESS_KEY_ID="<minio_access_key>" \
  --from-literal=S3_SECRET_ACCESS_KEY="<minio_secret_key>"
```

## Build & Run

```bash
cd services
./scripts/build_and_push-rule-engine.sh v0.8
./scripts/run-rule-engine.sh
./scripts/stop-rule-engine.sh
```

> ⚠️ `scripts/build_and_push-rule-engine.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/rule-engine/deployment.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires `gold.rule_engine_context` to already be populated (Spark `rule-engine-context-builder`) and `db/migrations/001_initial_schema.sql` applied (for `user_alert_rules`/`user_alert_events`/`users`) before startup — an empty context table means every quote is silently skipped (no symbol match), not an error.

## Known Issues

- **Empty context on startup is not fatal but effectively disables the service.** If `gold.rule_engine_context` is empty or unreachable at boot, `_context_cache` stays `{}` and every incoming quote is silently dropped at `handle_quote`'s first check (`ctx is None`) — no error is raised, no alert fires, and the only visible symptom is `/health` reporting `symbols_loaded: "0"`.
- Custom-rule cooldowns and prev-value tracking are **in-memory, per-replica** (`_last_fired`, `_prev_values` in `UserAlertProcessor`) — if this service is ever scaled beyond 1 replica, each replica would track its own cooldown state independently, allowing the same custom alert to fire once per replica within a single cooldown window. The current single-replica deployment (`k8s/rule-engine/deployment.yaml`) avoids this, but it's a real constraint on horizontal scaling.
- `RSI_14`/`BB_POSITION` custom-alert fields reflect the **prior trading day's** batch snapshot, not the live intraday value — alert messages built downstream in `alert-service` must state this explicitly (per `CLAUDE.md`'s Real-Time vs. Batch Field Distinction).

## Testing

Has a real `tests/` suite — run with `pytest` from `services/rule-engine/`:

- `test_rules.py` — all 6 system rules (trigger/no-trigger, MEDIUM/HIGH boundaries, zero-denominator guards)
- `test_custom_rules.py` — `get_field_value`/`evaluate_condition` for every `AlertField`/`AlertOperator`, including `CROSSES_UP`/`CROSSES_DOWN`
- `test_rule_orchestrator.py` — cooldown suppression behavior
- `test_user_alert_processor.py` — custom rule evaluation, cooldown, `ONCE`-frequency rule removal
- `test_custom_alert_publish.py` — publish-failure isolation (Kafka error must not propagate)
- `test_db_client.py` — repository/DB access layer
