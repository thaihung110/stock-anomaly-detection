## Spark Applications

Scala Spark jobs for the Stock Anomaly Detection pipeline.

### Structure

```
spark-application/
├── <app-name>/          # Scala source code + Dockerfile
│   ├── src/
│   ├── build.sbt
│   └── Dockerfile
├── k8s/                 # SparkApplication CRDs + ConfigMaps + Secret template
│   ├── spark-app-secrets.yaml
│   ├── company-info-loader-symbols-configmap.yaml
│   ├── ohlcv-daily-loader-symbols-configmap.yaml
│   └── <app>-spark-application.yaml
└── scripts/             # Build/push and run/stop scripts
    ├── build-and-push-<app>.sh
    ├── run-<app>.sh
    └── stop-<app>.sh
```

---

### Applications

| Application | Image | Description |
|---|---|---|
| `company-info-loader` | `hungvt0110/company-info-loader:v0.2` | Fetch company info from Finnhub API → `bronze.raw_company_info` |
| `ohlcv-daily-loader` | `hungvt0110/ohlcv-daily-loader:v0.2` | Load daily OHLCV from yfinance → `bronze.raw_ohlcv_daily` |
| `ohlcv-daily-cleaner` | `hungvt0110/ohlcv-daily-cleaner:v0.2` | Clean OHLCV → `silver.ohlcv_daily` |
| `news-ingest-stream` | `hungvt0110/news-ingest-stream:v2.7` | Structured Streaming: Kafka → `bronze.raw_news_articles` |
| `news-cleaner` | `hungvt0110/news-cleaner:v0.3` | Clean news → `silver.news_clean` |
| `rule-engine-context-builder` | `hungvt0110/rule-engine-context-builder:v0.2` | Build `gold.rule_engine_context` (20d rolling stats) |
| `dim-loader` | `hungvt0110/dim-loader:v0.5` | Load dimension tables (dim_symbol, dim_date, ...) |
| `fact-ohlcv-daily-builder` | `hungvt0110/fact-ohlcv-daily-builder:v0.2` | Build `gold.fact_ohlcv_daily` |
| `trades-ohlcv-stream` | `hungvt0110/trades-ohlcv-stream:v1.1` | Structured Streaming: Kafka trades → `silver.ohlcv_1min` |
| `sync-custom-alerts` | `hungvt0110/sync-custom-alerts:v0.1` | OLTP→OLAP bridge: PostgreSQL → `gold.fact_alert_history` |

---

### Prerequisites (REQUIRED before running any Spark app)

#### 1. Create Secret `spark-app-secrets`

All Spark applications mount the `spark-app-secrets` secret for credentials. If it does not exist, driver and executor pods will fail immediately with:

```
secret "spark-app-secrets" not found
```

This secret is **not created by Helm** — apply it manually:

```bash
# Edit the actual credential values in the file before applying
kubectl apply -f spark-application/k8s/spark-app-secrets.yaml -n stock-anomaly-detection
```

`k8s/spark-app-secrets.yaml` contains:

| Key | Description |
|---|---|
| `MINIO_ACCESS_KEY` | MinIO access key (default: `admin`) |
| `MINIO_SECRET_KEY` | MinIO secret key (default: `admin123`) |
| `GRAVITINO_OAUTH_CLIENT_SECRET` | Keycloak client secret for the `spark` client |
| `FINNHUB_API_KEY` | Finnhub API key — used by `company-info-loader` |

> **Security note**: `spark-app-secrets.yaml` contains real credentials. Do not commit real values to git — use a template and inject via CI/CD or Vault.

---

#### 2. Create ConfigMap for `company-info-loader` (REQUIRED)

`company-info-loader` reads the symbols list from ConfigMap `company-info-loader-symbols`. If it does not exist, the pod will fail at volume mount:

```
MountVolume.SetUp failed for volume "symbols-config": configmap "company-info-loader-symbols" not found
```

Create the ConfigMap before running:

```bash
kubectl apply -f spark-application/k8s/company-info-loader-symbols-configmap.yaml -n stock-anomaly-detection
```

This ConfigMap holds 50 symbols (AAPL, MSFT, GOOGL, ...) mounted as `/tmp/symbols.txt` inside driver and executor pods.

> **Note**: The symbols list must stay in sync with `finnhub-trades-producer/config.py`. When adding or removing symbols, update both files and re-apply the ConfigMap.

---

#### 3. Create ConfigMap for `ohlcv-daily-loader`

`ohlcv-daily-loader` requires ConfigMap `ohlcv-loader-symbols`:

```bash
kubectl apply -f spark-application/k8s/ohlcv-daily-loader-symbols-configmap.yaml -n stock-anomaly-detection
```

---

### Build and Push Docker Image

```bash
cd spark-application
./scripts/build-and-push-<app-name>.sh <version>

# Example:
./scripts/build-and-push-company-info-loader.sh v0.3
```

---

### Run and Stop

```bash
cd spark-application

# Run
./scripts/run-<app-name>.sh

# Stop
./scripts/stop-<app-name>.sh
```

Monitor logs:

```bash
kubectl logs -f -n stock-anomaly-detection <app-name>-driver
```

---

### Known Issues

#### `rule-engine-context-builder` fails with "0 rows produced" on weekends or early morning runs

**Symptom**

```
RuntimeException: overwritePartitions to gravitino_catalog.gold.rule_engine_context aborted:
0 rows produced. Check that fact_ohlcv_daily has data for the target date_key and dim_symbol is populated.
```

**Root cause**

`rule-engine-context-builder` defaults `asOfDateKey` to **UTC yesterday** (`LocalDate.now(ZoneOffset.UTC).minusDays(1)`). It then filters `fact_ohlcv_daily` for exactly that `date_key`.

Yahoo Finance returns OHLCV data only for **trading days** (Mon–Fri, excluding US market holidays). When the pipeline runs on a **Saturday or before NYSE open (~14:30 UTC)**, the most recent trading day in `fact_ohlcv_daily` is the **Friday before last**, not yesterday. The filter produces 0 rows and the job aborts.

Confirmed behaviour (2026-05-24, Saturday):
- `ohlcv-daily-loader` fetched data ending at `trade_date = 2026-05-22` (Friday)
- `defaultAsOfDateKey()` returned `20260523` (Friday) — no data for that date in the fact table
- Fix: set `AS_OF_DATE_KEY=20260522`

**Fix**

Override `AS_OF_DATE_KEY` in `k8s/rule-engine-context-builder-spark-application.yaml` to the last known trading day before running manually:

```yaml
- name: AS_OF_DATE_KEY
  value: "20260522"   # set to the last trading day in fact_ohlcv_daily
```

Remove or update this env var after the weekend so the production schedule resumes using the automatic UTC-yesterday default (leave the key absent for normal weekday runs).

**When this does NOT happen**

On a normal weekday run (pipeline triggered after NYSE close, ~21:00 UTC+), `ohlcv-daily-loader` will have fetched that day's data, `fact_ohlcv_daily` will contain the matching `date_key`, and `defaultAsOfDateKey()` will resolve correctly without any override.

---

### First-time Startup Order

```
1. Apply secrets + configmaps (see Prerequisites above)
2. company-info-loader        → bronze.raw_company_info
3. ohlcv-daily-loader         → bronze.raw_ohlcv_daily
4. ohlcv-daily-cleaner        → silver.ohlcv_daily
5. dim-loader                 → gold.dim_*
6. fact-ohlcv-daily-builder   → gold.fact_ohlcv_daily
7. rule-engine-context-builder → gold.rule_engine_context
8. news-ingest-stream         → bronze.raw_news_articles  (streaming, runs continuously)
9. news-cleaner               → silver.news_clean
10. trades-ohlcv-stream       → silver.ohlcv_1min         (streaming, runs continuously)
11. sync-custom-alerts        → gold.fact_alert_history
```