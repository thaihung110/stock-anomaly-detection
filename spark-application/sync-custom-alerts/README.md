# sync-custom-alerts

The OLTP → OLAP bridge for custom (user-defined) alerts: incrementally syncs `user_alert_events` from PostgreSQL into `gold.fact_alert_history` on Iceberg, using a watermark for exactly-once-ish incremental sync.

## Data Flow

```
PostgreSQL: user_alert_events (OLTP, source of truth for custom-alert runtime data)
    ↓ JDBC read, filtered by watermark (triggered_at > last_sync_at)
transform (map OLTP columns → fact table columns)
    ↓ Iceberg append (NOT upsert)
gravitino_gold.gold.fact_alert_history  (alert_source = 'user_custom')
    ↓ on success only
PostgreSQL: sync_watermarks.last_sync_at updated
```

Batch job, not streaming. Scheduled at 07:30 UTC per `CLAUDE.md`, after `build_rule_context`.

## Bridge Contract

This is the OLTP–OLAP bridge for the custom alert feature:

- **Source of truth (OLTP)**: `user_alert_events` in PostgreSQL — owned by the Rule Engine/Telegram Bot services, immutable event log.
- **Analytics destination (OLAP)**: `gold.fact_alert_history` in Iceberg — read-only analytical copy for dashboards/BI/historical analysis. **One-way flow only** — nothing is ever written back from Iceberg to PostgreSQL.
- **Sync policy**: incremental by watermark (`sync_watermarks` table, `job_name = 'sync-custom-alerts'`), not a fixed date-range query. This avoids the failure mode of a fixed `WHERE triggered_at >= CURRENT_DATE - INTERVAL '1 day'` query silently missing events if the job runs late, reruns, or the schedule changes.
- **Freshness expectation**: this is an analytics pipeline, not the alert-delivery path — real-time Telegram delivery goes directly from the Rule Engine, independent of this job. Dashboard data is expected to lag by up to one batch cycle (daily).

### Column mapping

| PostgreSQL `user_alert_events` | Iceberg `fact_alert_history` | Notes |
|---|---|---|
| `event_id` | `alert_id` (cast to string) | |
| `user_id` | `user_id` (cast to string) | Added per a "Phase 3" backend change — lets each custom-alert row carry its owner |
| `symbol` | `symbol` | |
| `triggered_at` | `event_ts` | Formatted `yyyy-MM-dd'T'HH:mm:ss'Z'` |
| `field_snapshot`, `operator_snapshot`, `threshold_snapshot` | `rule_name` | Concatenated into one human-readable string, e.g. `"price_zscore > 3.0"` |
| `triggered_value` | `triggered_value` (cast double) | |
| `threshold_snapshot` | `threshold` (cast double) | Also reused separately as a numeric column |
| constant | `alert_source = "user_custom"` | Distinguishes from system-generated alerts in the same fact table |
| constant | `severity = "INFO"` | Hardcoded — custom alerts don't currently carry a severity level from the OLTP side |
| job run time | `written_at` | Formatted `yyyy-MM-dd'T'HH:mm:ss'Z'` |

## Pipeline Steps

`SyncCustomAlertsPipeline` (`pipeline/SyncCustomAlertsPipeline.scala`):

1. **`readWatermark`**: opens a raw JDBC connection (not Spark's JDBC datasource — a plain `java.sql.Connection`, with `SET TIME ZONE 'UTC'` issued immediately after connecting) and reads `sync_watermarks.last_sync_at` for `job_name = 'sync-custom-alerts'`. If no row exists yet, defaults to `Timestamp(0L)` (epoch) — first run syncs everything.
   - Connection retries up to 3 times (2s delay) — the code comment notes Postgres can transiently refuse connections right after other Spark apps in the same DAG run just finished (connection pool cold start).
2. **`readNewEvents`**: Spark JDBC read of `user_alert_events`, filtered to `triggered_at > since`, ordered by `triggered_at`.
3. **`transform`**: maps OLTP columns to fact-table columns per the contract table above.
4. **`run`** orchestrates the critical ordering, with an explicit invariant documented in the source:
   - Compute `newWatermark = max(triggered_at)` from the read batch **before** writing anything.
   - `writeToIceberg` (plain Iceberg `.append()` — **not** `MERGE INTO`, this is an append-only fact log).
   - **Only if the write succeeds**, call `updateWatermark`.
   - If the write fails, the watermark is untouched — safe to retry, no data loss.
   - If the write succeeds but the watermark update fails, the next run will **re-sync and duplicate** those rows — the code logs this explicitly as requiring manual dedup investigation; it is a known, accepted gap rather than something silently swallowed.
   - If there are zero new rows, the job logs and exits without writing anything or touching the watermark.

## Configuration

Env vars read by `AppConfig.fromEnv()`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GRAVITINO_URI`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAVITINO_OAUTH_CLIENT_SECRET` | Yes | — | Same shape as every app |
| `GRAVITINO_OAUTH_SERVER_URI` / `_TOKEN_PATH` / `_SCOPE` | No | `http://openhouse-keycloak` / `realms/iceberg/protocol/openid-connect/token` / `gravitino` | |
| `JDBC_URL` | Yes | — | e.g. `jdbc:postgresql://openhouse-postgresql-primary:5432/stock_anomaly` |
| `PG_USER` | Yes | — | e.g. `stock_user` |
| `PG_PASSWORD` | Yes | — | From `spark-app-secrets` |
| `FACT_TABLE` | No | `gravitino_gold.gold.fact_alert_history` | |
| `WATERMARK_JOB_NAME` | No | `sync-custom-alerts` | Row key in `sync_watermarks` |

This is the only app that talks to PostgreSQL as a **data source** (not just as Gravitino's/Keycloak's backing store) — it's the sole consumer of the OLTP `stock_anomaly` database from within `spark-application/`.

## Catalog / Connection Config

Registers `gravitino_gold` for the Iceberg write side. The PostgreSQL side uses a direct JDBC connection (both a raw `java.sql.Connection` for the watermark read/write, and Spark's JDBC datasource for the bulk event read) — not routed through Gravitino at all, since `user_alert_events` and `sync_watermarks` are OLTP tables, not part of the Iceberg lakehouse.

## Kubernetes Resource Sizing

From `k8s/sync-custom-alerts-spark-application.yaml`:

- **Driver**: 1 core, 512Mi memory + 256Mi overhead
- **Executor**: 1 instance, 1 core, 512Mi memory + 256Mi overhead
- **`restartPolicy`**: `OnFailure`, 3 retries, 10s interval; TTL 3600s
- `spark.sql.session.timeZone=UTC` set explicitly — important here because timestamp comparisons against the watermark must be timezone-consistent between Spark, the JDBC connection, and Postgres

## Build & Run

```bash
cd spark-application
./scripts/build-and-push-sync-custom-alerts.sh v0.4
./scripts/run-sync-custom-alerts.sh
./scripts/stop-sync-custom-alerts.sh
```

> ⚠️ `scripts/build-and-push-sync-custom-alerts.sh` pushes to the hardcoded `hungvt0110` Docker Hub registry — edit `REGISTRY` in that script to your own before running, and update the `image:` field in `k8s/sync-custom-alerts-spark-application.yaml` to match. See the [top-level README](../README.md#build-and-push-docker-image) for details.

Requires the OLTP `stock_anomaly` database (with `user_alert_events` and `sync_watermarks` tables) to already exist — see `infra/k8s/storage/README.md`'s PostgreSQL section for how these get created.

## Known Issues

The write-then-update-watermark sequence has a documented (not silently handled) gap: if the Iceberg append commits but the subsequent watermark update fails, the next run will re-read and re-append the same events, producing duplicates in `fact_alert_history`. This requires manual dedup — there's no automatic reconciliation. Monitor logs for `"Iceberg write committed but watermark update failed"` to catch this case.

## Testing

No automated tests yet — `src/test/scala/.../` is an empty placeholder (`.gitkeep` only).
