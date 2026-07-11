## Spark Applications

Scala Spark jobs for the Stock Anomaly Detection pipeline.

### Structure

```
spark-application/
├── <app-name>/          # Scala source code + Dockerfile + README.md
│   ├── src/
│   ├── build.sbt
│   ├── Dockerfile
│   └── README.md
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

### Shared Architecture

Every app in this directory follows the same code shape (see any app's `README.md` for its specifics):

```
<app>/src/main/scala/com/stockanomalydetection/<package>/
├── <AppName>.scala              # entrypoint: builds SparkSession, wires config → catalog → pipeline
├── config/AppConfig.scala       # case class populated from env vars via AppConfig.fromEnv()
├── config/CatalogConfigurator.scala  # registers the Gravitino Iceberg REST catalog(s) + ensures target tables exist
└── pipeline/<Name>Pipeline.scala     # the actual business logic, as pure functions (read → transform → write)
```

All apps authenticate to Gravitino's Iceberg REST catalog via OAuth2 client-credentials against Keycloak (client `spark`), and to MinIO via static access/secret keys — both injected through the same handful of env vars (`GRAVITINO_URI`, `GRAVITINO_OAUTH_CLIENT_SECRET`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`). Batch apps use `MERGE INTO` or `overwritePartitions()` for idempotent re-runs; streaming apps use Iceberg's streaming sink with a per-app checkpoint location.

---

### Applications

| Application                   | Layer  | Image (pattern: `<your-registry>/<app-name>:<tag>`) | Description                                                                               | Docs                                            |
| ----------------------------- | ------ | ----------------------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `company-info-loader`         | Bronze | `<your-registry>/company-info-loader:<tag>`           | Fetch company info from Finnhub API → `bronze.raw_company_info`                           | [README](company-info-loader/README.md)         |
| `ohlcv-daily-loader`          | Bronze | `<your-registry>/ohlcv-daily-loader:<tag>`            | Load daily OHLCV from yfinance (watermark-incremental) → `bronze.raw_ohlcv_daily`         | [README](ohlcv-daily-loader/README.md)          |
| `news-ingest-stream`          | Bronze | `<your-registry>/news-ingest-stream:<tag>`            | Structured Streaming: Kafka news → `bronze.raw_news_articles`                             | [README](news-ingest-stream/README.md)          |
| `ohlcv-daily-cleaner`         | Silver | `<your-registry>/ohlcv-daily-cleaner:<tag>`           | Clean OHLCV → `silver.ohlcv_daily`                                                        | [README](ohlcv-daily-cleaner/README.md)         |
| `news-cleaner`                | Silver | `<your-registry>/news-cleaner:<tag>`                  | Dedupe + clean news → `silver.news_clean`                                                 | [README](news-cleaner/README.md)                |
| `trades-ohlcv-stream`         | Silver | `<your-registry>/trades-ohlcv-stream:<tag>`           | Structured Streaming: Kafka trades → 1-min bars → `silver.ohlcv_1min`                     | [README](trades-ohlcv-stream/README.md)         |
| `dim-loader`                  | Gold   | `<your-registry>/dim-loader:<tag>`                    | Load/refresh dimension tables (`dim_symbol` SCD2, `dim_date`, `dim_time`, static lookups) | [README](dim-loader/README.md)                  |
| `fact-ohlcv-daily-builder`    | Gold   | `<your-registry>/fact-ohlcv-daily-builder:<tag>`      | Compute rolling stats + technical indicators → `gold.fact_ohlcv_daily`                    | [README](fact-ohlcv-daily-builder/README.md)    |
| `rule-engine-context-builder` | Gold   | `<your-registry>/rule-engine-context-builder:<tag>`   | Single-day snapshot for the Rule Engine → `gold.rule_engine_context`                      | [README](rule-engine-context-builder/README.md) |
| `sync-custom-alerts`          | Gold   | `<your-registry>/sync-custom-alerts:<tag>`            | OLTP→OLAP bridge: PostgreSQL → `gold.fact_alert_history`                                  | [README](sync-custom-alerts/README.md)          |

> `<your-registry>` is whatever you set `REGISTRY` to in `scripts/build-and-push-<app>.sh` (Docker Hub username, GHCR path, private registry host, etc.) — see "Build and Push Docker Image" below. It defaults to the original author's personal Docker Hub (`hungvt0110`) in every script; you need to change it before building your own images.

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

| Key                             | Description                                                                              |
| ------------------------------- | ---------------------------------------------------------------------------------------- |
| `MINIO_ACCESS_KEY`              | MinIO access key (default: `admin`)                                                      |
| `MINIO_SECRET_KEY`              | MinIO secret key (default: `admin123`)                                                   |
| `GRAVITINO_OAUTH_CLIENT_SECRET` | Keycloak client secret for the `spark` client                                            |
| `FINNHUB_API_KEY`               | Finnhub API key — used by `company-info-loader`                                          |
| `PG_PASSWORD`                   | PostgreSQL password for the `stock_anomaly` OLTP database — used by `sync-custom-alerts` |

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

Each `scripts/build-and-push-<app>.sh` hardcodes its own registry — it does **not** read your Docker config or take the registry as an argument:

```bash
# Contents of every build-and-push-<app>.sh (only SERVICE_NAME differs per app)
VERSION="$1"
REGISTRY="hungvt0110"                    # ← the original author's Docker Hub — change this
SERVICE_NAME="<app-name>"
IMAGE_NAME="$REGISTRY/$SERVICE_NAME:$VERSION"

docker build -f "<app-name>/Dockerfile" -t "$IMAGE_NAME" "<app-name>/"
docker push "$IMAGE_NAME"
```

**Before building for the first time**, for each app you plan to run:

1. Open `scripts/build-and-push-<app>.sh` and change `REGISTRY="hungvt0110"` to your own registry (e.g. your Docker Hub username, a GHCR path like `ghcr.io/<you>`, or a private registry host).
2. Update the `image:` field in the matching `k8s/<app>-spark-application.yaml` to the **same** registry — the SparkApplication CRD pulls whatever is hardcoded there; it has no knowledge of what the build script pushed.
3. Make sure you're authenticated to that registry (`docker login <registry>`) before pushing.

Then build and push:

```bash
cd spark-application
./scripts/build-and-push-<app-name>.sh <version>

# Example (after editing REGISTRY in the script to your own):
./scripts/build-and-push-company-info-loader.sh v0.4
# → builds and pushes <your-registry>/company-info-loader:v0.4
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

> ⚠️ Not every script follows the `<app-name>` pattern exactly: `news-ingest-stream` uses `run-news-ingest.sh`/`stop-news-ingest.sh`, and `trades-ohlcv-stream` uses `run-trades-ohlcv.sh`/`stop-trades-ohlcv.sh`. Check `scripts/` if a `run-<app-name>.sh` doesn't exist.

Monitor logs:

```bash
kubectl logs -f -n stock-anomaly-detection <app-name>-driver
```

---

### Known Issues

Full detail lives in each app's own README. Summary of what's currently flagged:

| App                                    | Issue                                                                                                                                                                                                                                                                                                  |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `rule-engine-context-builder`          | Fails with "0 rows produced" on weekend/early-morning runs (UTC-yesterday default outruns actual trading-day data) — [see README](rule-engine-context-builder/README.md#known-issues). The current `k8s/` YAML still has a hardcoded `AS_OF_DATE_KEY` left over from the last manual fix.              |
| `dim-loader`                           | `k8s/dim-loader-spark-application.yaml` maps `AWS_ACCESS_KEY_ID` to the wrong secret key (`MINIO_SECRET_KEY` instead of `MINIO_ACCESS_KEY`) — [see README](dim-loader/README.md#known-issues). Also has stale-but-harmless `AppConfig.scala` OAuth defaults that don't match what's actually deployed. |
| `sync-custom-alerts`                   | If the Iceberg write succeeds but the watermark update fails, the next run re-syncs and duplicates rows — documented, not automatically handled — [see README](sync-custom-alerts/README.md#known-issues).                                                                                             |
| `ohlcv-daily-cleaner` / `news-cleaner` | Both do a **full table read** every run (no incremental filter) — cost grows with total accumulated history, not just new data.                                                                                                                                                                        |

---

### Testing

**No app in this directory has automated tests yet.** Every `<app>/src/test/scala/.../` directory contains only a `.gitkeep` placeholder. This applies uniformly across all 10 apps — treat any pipeline change as untested until this changes.

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
