# Bronze Layer SparkApplication Flow trên ArgoCD

## Flow Tổng Quan

```
Airflow → Git → ArgoCD → Kubernetes → Spark Operator → Spark Jobs
```

## Chi Tiết Từng Bước

### 1. Airflow Generate Manifest

- **Trigger**: Airflow DAG được trigger (từ Kafka event hoặc schedule)
- **Action**: Airflow đọc template từ `spark/bronze-layer/template.yaml`
- **Generate**: Tạo SparkApplication manifest với variables:
  - `{{ job_id }}` → `dag-run-123`
  - `{{ input_path }}` → `s3a://raw/nyc_taxi_data/yellow_tripdata.csv`
  - `{{ table_name }}` → `taxi_trips`
- **Output**: File `spark/bronze-layer/jobs/bronze-dag-run-123.yaml`

### 2. Airflow Push to Git

- **Action**: Airflow commit và push manifest vào Git repository
- **Location**: `infra/k8s/compute/applications/spark/bronze-layer/jobs/`
- **Result**: Git repository có manifest mới

### 3. ArgoCD Detect Change

- **Polling**: ArgoCD poll Git repo mỗi 3 phút (hoặc webhook trigger)
- **Detection**: ArgoCD phát hiện file mới trong `spark/bronze-layer/jobs/`
- **Status**: Application chuyển sang `OutOfSync`

### 4. ArgoCD Sync to Kubernetes

- **Action**: ArgoCD đọc manifest từ Git
- **Apply**: `kubectl apply` SparkApplication CRD vào namespace `data-platform`
- **Result**: SparkApplication resource được tạo trong Kubernetes

### 5. Spark Operator Watch & Create

- **Watch**: Spark Operator đang watch SparkApplication CRDs
- **Detect**: Phát hiện SparkApplication mới
- **Create Pods**:
  - Driver pod: `spark-driver-bronze-layer-dag-run-123`
  - Executor pods: `spark-exec-1-bronze-layer-dag-run-123`, etc.

### 6. Spark Job Execution

- **Driver**: Load Spark config, connect to Lakekeeper, read from MinIO
- **Transform**: Process data theo bronze layer logic
- **Write**: Write to MinIO warehouse (Iceberg table `bronze.taxi_trips`)
- **Register**: Register table metadata trong Lakekeeper catalog

### 7. ArgoCD Monitor & Self-Heal

- **Monitor**: ArgoCD liên tục monitor Git vs Kubernetes state
- **Self-Heal**: Nếu có drift (resource bị xóa/thay đổi), ArgoCD tự động sync lại
- **Prune**: Nếu file bị xóa khỏi Git, ArgoCD tự động xóa resource trong K8s

## Timeline

```
T0:     Airflow DAG triggered
T0+5s:  Manifest generated
T0+10s: Manifest pushed to Git
T0+13s: ArgoCD detects change (polling)
T0+15s: ArgoCD syncs to Kubernetes
T0+20s: Spark Operator creates pods
T0+30s: Spark job starts execution
T0+5m:  Spark job completes
T0+5m:  ArgoCD shows status: Synced
```

## Key Features

- **Automated Sync**: Tự động sync khi Git thay đổi
- **Self-Heal**: Tự động sync lại nếu K8s drift từ Git
- **Prune**: Tự động xóa resource không còn trong Git
- **Ignore Status**: Bỏ qua status changes để tránh sync conflicts
