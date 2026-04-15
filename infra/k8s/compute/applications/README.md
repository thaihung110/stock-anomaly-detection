# Spark Applications - Crypto Data Pipeline

Kubernetes manifests for Spark applications processing crypto trading data.

## 📁 Directory Structure

```
applications/
└── spark/
    ├── bronze-layer/
    │   └── jobs/
    │       └── load-crypto-bronze.yaml          # Streaming: Kafka → Bronze Iceberg
    │
    └── silver-layer/
        └── jobs/
            └── transform-crypto-silver-batch.yaml  # Batch: Bronze → Silver OHLCV
```

---

## 🚀 Spark Jobs

### 1. Load Crypto Bronze (Streaming)

**File**: `spark/bronze-layer/jobs/load-crypto-bronze.yaml`

**Purpose**: Real-time ingestion of crypto trade data from Kafka to Bronze Iceberg table

**Type**: Streaming (continuous, long-running)

**Key Specifications**:

```yaml
metadata:
  name: load-crypto-bronze
  namespace: default

spec:
  type: Python
  mode: cluster
  sparkVersion: "3.5.0"
  image: hungvt0110/load-crypto-bronze:latest
  mainApplicationFile: local:///app/src/main.py

  arguments:
    - "market-data.finnhub.crypto-trades.bronze" # Kafka topic
    - "bronze" # Database
    - "crypto_trades_raw" # Table name
```

**Environment Variables**:

- `KAFKA_BOOTSTRAP_SERVERS`: Kafka broker address
- `KAFKA_SASL_USERNAME/PASSWORD`: SASL authentication
- `CATALOG_URL`: Lakekeeper catalog endpoint
- `CLIENT_ID/CLIENT_SECRET`: OAuth credentials
- `TRIGGER_INTERVAL`: Streaming trigger (default: 10 seconds)
- `CHECKPOINT_LOCATION`: Checkpoint path for recovery

**Resources**:

- **Driver**: 1 core, 2GB RAM
- **Executors**: 2 instances, 2 cores each, 2GB RAM

**Deployment**:

```bash
# Start streaming job
kubectl apply -f spark/bronze-layer/jobs/load-crypto-bronze.yaml

# Or use helper script
cd ../../scripts
./start_load_crypto_bronze.sh
```

**Monitoring**:

```bash
# Check status
kubectl get sparkapplication load-crypto-bronze

# View driver logs
kubectl logs -l spark-role=driver,spark-app-name=load-crypto-bronze -f

# View executor logs
kubectl logs -l spark-role=executor,spark-app-name=load-crypto-bronze -f
```

---

### 2. Transform Crypto Silver Batch

**File**: `spark/silver-layer/jobs/transform-crypto-silver-batch.yaml`

**Purpose**: Daily batch aggregation of crypto trades into hourly OHLCV candles

**Type**: Batch (triggered by Airflow daily at 2 AM)

**Key Specifications**:

```yaml
metadata:
  name: transform-crypto-silver-batch
  namespace: default

spec:
  type: Python
  mode: cluster
  sparkVersion: "3.5.0"
  image: hungvt0110/transform-crypto-silver-batch:latest
  mainApplicationFile: local:///app/src/main.py

  arguments:
    - "2025-12-28" # Start date (YYYY-MM-DD)
    - "2025-12-29" # End date (YYYY-MM-DD)
```

**Environment Variables**:

**Bronze Catalog**:

- `BRONZE_CATALOG_URL`: Lakekeeper catalog endpoint
- `BRONZE_CLIENT_ID/SECRET`: OAuth credentials
- `BRONZE_WAREHOUSE`: Warehouse name (bronze)

**Silver Catalog**:

- `SILVER_CATALOG_URL`: Lakekeeper catalog endpoint
- `SILVER_CLIENT_ID/SECRET`: OAuth credentials
- `SILVER_WAREHOUSE`: Warehouse name (silver)

**Resources**:

- **Driver**: 1 core, 2GB RAM
- **Executors**: 2 instances, 2 cores each, 2GB RAM

**Deployment**:

**Manual** (for testing):

```bash
# Edit dates in manifest
vim spark/silver-layer/jobs/transform-crypto-silver-batch.yaml

# Apply
kubectl apply -f spark/silver-layer/jobs/transform-crypto-silver-batch.yaml

# Or use helper script
cd ../../scripts
./start_transform_crypto_silver_batch.sh
```

**Automated** (production):

```bash
# Via Airflow DAG (recommended)
# DAG: crypto-ohlcv-silver-batch
# Schedule: Daily at 2 AM
# See: ../../../../airflow-dags-deployment/README.md
```

**Monitoring**:

```bash
# Check status
kubectl get sparkapplication transform-crypto-silver-batch

# View driver logs
kubectl logs -l spark-role=driver,spark-app-name=transform-crypto-silver-batch -f

# Describe for details
kubectl describe sparkapplication transform-crypto-silver-batch
```

---

## 📊 Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     CRYPTO DATA PIPELINE                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────┐
│ Finnhub API │ (WebSocket)
│  Producer   │
└──────┬──────┘
       │ Avro messages
       ▼
┌─────────────┐
│    Kafka    │ market-data.finnhub.crypto-trades.bronze
│   Topic     │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  BRONZE LAYER - Streaming Job (load-crypto-bronze.yaml)     │
│                                                              │
│  • Reads from Kafka (SASL auth)                             │
│  • Decodes Avro messages                                    │
│  • Transforms to structured format                          │
│  • Writes to Iceberg table                                  │
│  • Checkpoint-based recovery                                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Bronze Iceberg  │
                  │ crypto_trades   │
                  │     _raw        │
                  │                 │
                  │ Partitioned by: │
                  │ - trade_date    │
                  │ - exchange      │
                  └────────┬────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ SILVER LAYER - Batch Job (transform-crypto-silver-batch)    │
│                                                              │
│  • Reads Bronze data (by date range)                        │
│  • 1-hour OHLCV aggregation                                 │
│  • Calculates VWAP, price changes                           │
│  • Writes to Silver Iceberg table                           │
│  • Triggered daily by Airflow (2 AM)                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Silver Iceberg  │
                  │ crypto_ohlcv_1h │
                  │                 │
                  │ Partitioned by: │
                  │ - agg_date      │
                  │ - exchange      │
                  └─────────────────┘
```

---

## 🔧 Configuration Management

### Updating Job Configurations

**Bronze Streaming Job**:

```bash
# 1. Edit manifest
vim spark/bronze-layer/jobs/load-crypto-bronze.yaml

# 2. Delete existing job
kubectl delete sparkapplication load-crypto-bronze

# 3. Reapply
kubectl apply -f spark/bronze-layer/jobs/load-crypto-bronze.yaml
```

**Silver Batch Job**:

For Airflow-managed jobs, update ConfigMap:

```bash
# 1. Edit manifest
vim spark/silver-layer/jobs/transform-crypto-silver-batch.yaml

# 2. Recreate ConfigMap
cd ../../scripts
./create_spark_manifests_configmap.sh

# 3. Next Airflow run will use updated manifest
```

### Common Configuration Changes

**Increase Resources**:

```yaml
driver:
  cores: 2 # Increase from 1
  memory: "4g" # Increase from 2g

executor:
  cores: 4 # Increase from 2
  instances: 4 # Increase from 2
  memory: "4g" # Increase from 2g
```

**Change Kafka Topic**:

```yaml
arguments:
  - "new-topic-name" # Update first argument
  - "bronze"
  - "crypto_trades_raw"
```

**Adjust Trigger Interval** (Bronze job):

```yaml
env:
  - name: TRIGGER_INTERVAL
    value: "30 seconds" # Increase from 10 seconds
```

---

## 🛠️ Troubleshooting

### Common Issues

**Job stuck in SUBMITTED state**:

```bash
# Check Spark Operator logs
kubectl logs -n spark-operator -l app.kubernetes.io/name=spark-operator

# Check driver pod events
kubectl describe pod -l spark-role=driver,spark-app-name=load-crypto-bronze
```

**Driver pod CrashLoopBackOff**:

```bash
# View driver logs
kubectl logs -l spark-role=driver,spark-app-name=load-crypto-bronze

# Common causes:
# - Missing/incorrect credentials
# - Lakekeeper unreachable
# - Kafka connection issues
```

**Out of memory errors**:

```bash
# Increase driver/executor memory in manifest
# Also increase memoryOverhead (typically 10-20% of memory)
```

**Checkpoint corruption** (Bronze job):

```bash
# Delete checkpoint and restart
# WARNING: This will restart from latest Kafka offsets
kubectl delete sparkapplication load-crypto-bronze
# Edit manifest to change CHECKPOINT_LOCATION or clear existing checkpoint
kubectl apply -f spark/bronze-layer/jobs/load-crypto-bronze.yaml
```

---

## 📚 References

- [Spark Operator Documentation](https://github.com/GoogleCloudPlatform/spark-on-k8s-operator)
- [SparkApplication CRD Spec](https://github.com/GoogleCloudPlatform/spark-on-k8s-operator/blob/master/docs/api-docs.md)
- [Bronze Job Source Code](../../../../spark-jobs/load-crypto-bronze/README.md)
- [Silver Job Source Code](../../../../spark-jobs/transform-crypto-silver-batch/README.md)
- [Airflow DAG Documentation](../../../../airflow-dags-deployment/README.md)
