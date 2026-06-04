## Orchestration (Helm/K8s)

Thư mục dùng để triển khai trực tiếp (không ArgoCD) các thành phần orchestration.

### Cấu trúc

- `helm/` chứa chart đã pull (vd. `helm/airflow`, `helm/kafka`)
- `config/` chứa values đã tùy biến (vd. `config/airflow.yaml`, `config/kafka.yaml`)
- `scripts/` chứa script cài/gỡ (vd. `install_airflow.sh`, `install_kafka.sh`)

---

### Kafka

- Release: `openhouse-kafka`
- Namespace: `default`
- Mode: KRaft (không cần Zookeeper)

**Cấu hình trong `config/kafka.yaml`:**

```yaml
# Controller (KRaft mode)
controller:
  replicaCount: 1 # Số lượng controller pod
  controllerOnly: true # Controller chỉ quản lý metadata, không xử lý message
  persistence:
    enabled: true # Bật lưu trữ persistent cho data
    storageClass: "standard" # StorageClass của K8s
  logPersistence:
    enabled: true # Bật lưu trữ persistent cho logs
    storageClass: "standard"

# Broker
broker:
  replicaCount: 1 # Số lượng broker pod (scale theo nhu cầu)
  persistence:
    enabled: true # Bật lưu trữ persistent cho data
    storageClass: "standard"
  logPersistence:
    enabled: true # Bật lưu trữ persistent cho logs
    storageClass: "standard"

# Storage mặc định
defaultStorageClass: "standard" # StorageClass mặc định cho các PVC

# Listeners (protocol kết nối)
listeners:
  client:
    containerPort: 9092 # Port cho client kết nối
    protocol: PLAINTEXT # Không mã hóa, không auth (SASL_PLAINTEXT nếu cần auth)
    name: CLIENT
  controller:
    containerPort: 9093 # Port cho controller (KRaft internal)
    protocol: PLAINTEXT
    name: CONTROLLER
  interbroker:
    containerPort: 9094 # Port giao tiếp giữa các broker
    protocol: PLAINTEXT
    name: INTERNAL
  external:
    containerPort: 9095 # Port cho external client (nếu expose NodePort/LoadBalancer)
    protocol: PLAINTEXT
    name: EXTERNAL
```

Triển khai:

```bash
cd infra/k8s/orchestration
./scripts/install_kafka.sh
```

Gỡ cài:

```bash
cd infra/k8s/orchestration
./scripts/uninstall_kafka.sh
```

Lưu ý:

- Đảm bảo có StorageClass `standard` trong cluster.
- Listener protocol: `PLAINTEXT` (tắt SASL để đơn giản hóa kết nối).
- Để scale broker/controller: tăng `broker.replicaCount` và `controller.replicaCount` trong `config/kafka.yaml`.
- **Cấu hình Single Node**: Khi chạy 1 broker/controller, CẦN thiết lập các tham số sau trong `overrideConfiguration` hoặc `extraEnvVars` để tránh lỗi broker crash hoặc không consume được:
  - `offsets.topic.replication.factor=1`: Topic nội bộ `__consumer_offsets` chỉ cần 1 bản sao (mặc định là 3), tránh lỗi consumer không tìm thấy leader.
  - `transaction.state.log.replication.factor=1`: Transaction log chỉ cần 1 bản sao (mặc định là 3).
  - `transaction.state.log.min.isr=1`: Số lượng replica tối thiểu đồng bộ là 1.
  - `KAFKA_CFG_AUTO_CREATE_TOPICS_ENABLE=true`: Cho phép tự động tạo topic (bao gồm cả internal topic) nếu chưa tồn tại.

---

### Kafka UI

- Release: `openhouse-kafka-ui`
- Namespace: `default`
- Kết nối tới: `openhouse-kafka` cluster

**Cấu hình trong `config/kafka-ui.yaml`:**

```yaml
# Replicas
replicaCount: 1 # Số lượng pod Kafka UI

# Image
image:
  registry: docker.io
  repository: provectuslabs/kafka-ui # Image của Kafka UI
  pullPolicy: IfNotPresent
  tag: "" # Dùng tag mặc định từ chart appVersion

# Kết nối Kafka cluster
yamlApplicationConfig:
  kafka:
    clusters:
      - name: openhouse-kafka # Tên hiển thị trong UI
        bootstrapServers: openhouse-kafka:9092 # Service:Port của Kafka broker
  auth:
    type: disabled # Tắt authentication cho Kafka UI (có thể dùng LOGIN, OAUTH2...)
  management:
    health:
      ldap:
        enabled: false # Tắt health check cho LDAP

# Ingress
ingress:
  enabled: true # Bật ingress để truy cập từ bên ngoài
  ingressClassName: "nginx" # Ingress controller class
  host: "openhouse.kafka-ui.test" # Domain truy cập
  path: "/" # Path prefix
  pathType: "Prefix" # Kiểu path matching
  tls:
    enabled: false # Tắt TLS (dùng HTTP, có thể bật HTTPS với cert)
    secretName: "" # Secret chứa TLS cert (nếu enabled)
```

Triển khai:

```bash
cd infra/k8s/orchestration
./scripts/install_kafka_ui.sh
```

Gỡ cài:

```bash
cd infra/k8s/orchestration
./scripts/uninstall_kafka_ui.sh
```

Lưu ý:

- Kafka UI yêu cầu Kafka đã chạy và listener dùng `PLAINTEXT` protocol.
- Truy cập UI qua `http://openhouse.kafka-ui.test` (cần cấu hình hosts hoặc DNS).
- Để kết nối nhiều cluster: thêm vào `yamlApplicationConfig.kafka.clusters[]` trong `config/kafka-ui.yaml`.

---

### Airflow

- Release: `openhouse-airflow`
- Namespace: `default`
- Version: `3.0.2`
- Tài khoản mặc định: `admin` / `admin`

**Cấu hình trong `config/airflow.yaml`:**

```yaml
# Ingress (API Server)
ingress:
  apiServer:
    enabled: true
    host: "openhouse.airflow.test"
    hosts:
      - name: "openhouse.airflow.test"
        tls:
          enabled: false
    ingressClassName: "nginx"
    path: "/"
    pathType: "ImplementationSpecific"

# PostgreSQL (subchart)
postgresql:
  enabled: true
  image:
    registry: docker.io
    repository: bitnamilegacy/postgresql
    tag: 16.4.0-debian-12-r4
  auth:
    enablePostgresUser: true
    postgresPassword: postgres
    username: "postgres"
    password: "postgres"
  primary:
    persistence:
      enabled: true
      storageClass: standard
      size: 8Gi

# Database connection
data:
  metadataConnection:
    user: postgres
    pass: postgres
    protocol: postgresql
    host: openhouse-airflow-postgresql
    port: 5432
    db: postgres
    sslmode: disable

# Redis (subchart, broker cho CeleryExecutor)
redis:
  enabled: true
  persistence:
    enabled: true
    storageClass: standard
    size: 1Gi

# Executor
executor: "CeleryExecutor"

# API Server startup probe
apiServer:
  startupProbe:
    initialDelaySeconds: 60
    timeoutSeconds: 20
    failureThreshold: 30
    periodSeconds: 10
```

Triển khai:

```bash
cd infra/k8s/orchestration
./scripts/install_airflow.sh
```

Gỡ cài:

```bash
cd infra/k8s/orchestration
./scripts/uninstall_airflow.sh
```

Lưu ý:

- Ingress trỏ tới API server (Airflow 3.x không dùng webserver riêng).
- Đảm bảo có StorageClass `standard` và Ingress Controller `nginx`.
- Để scale worker/scheduler: chỉnh `workers.replicas`, `scheduler.replicas` trong `config/airflow.yaml`.

#### Prerequisite: SSH secret cho git-sync (BẮT BUỘC trước khi cài)

Các pods `worker`, `triggerer`, `dag-processor` dùng git-sync để clone DAGs qua SSH từ `git@github.com:thaihung110/airflow-dags.git`. Nếu secret `airflow-ssh-secret` chưa tồn tại, 3 pods này sẽ bị stuck ở `Init:0/2` với lỗi:

```
MountVolume.SetUp failed for volume "git-sync-ssh-key": secret "airflow-ssh-secret" not found
```

Secret này **không được tạo bởi Helm** — phải tạo thủ công trước khi chạy `install_airflow.sh`:

```bash
# 1. Tạo SSH keypair cho git-sync (không passphrase)
ssh-keygen -t ed25519 -C "airflow-gitsync" -f ~/.ssh/airflow_gitsync -N ""

# 2. Tạo secret trong namespace
kubectl create secret generic airflow-ssh-secret \
  --from-file=gitSshKey=/home/hungvt/.ssh/airflow_gitsync \
  -n stock-anomaly-detection

# 3. Lấy public key để add vào GitHub
cat ~/.ssh/airflow_gitsync.pub
```

Sau đó add public key vào GitHub repo `thaihung110/airflow-dags`:

- Repo → **Settings** → **Deploy keys** → **Add deploy key**
- Paste nội dung public key, tên: `airflow-gitsync`
- Chỉ cần **Read access**

Sau khi secret tồn tại, các pods sẽ tự khởi động lại và hoàn thành init containers.

---

### RBAC

RBAC resources are in `rbac/` — required for Airflow to submit and monitor Spark jobs via the Spark Operator.

#### Apply

```bash
kubectl apply -f infra/k8s/orchestration/rbac/airflow-spark-rbac.yaml
kubectl apply -f infra/k8s/orchestration/rbac/spark-submit-clusterrole.yaml
kubectl apply -f infra/k8s/orchestration/rbac/spark-submit-clusterrolebinding.yaml
```

#### Resources

**`airflow-spark-rbac.yaml`** — Role + RoleBinding scoped to `stock-anomaly-detection` namespace.

- Role `airflow-spark-operator`: grants `create/get/list/watch/delete/patch/update` on `sparkapplications` (Spark Operator CRD).
- Bound to: `openhouse-airflow-worker`, `openhouse-airflow-triggerer` (namespace `stock-anomaly-detection`).

**`spark-submit-clusterrole.yaml`** — ClusterRole `spark-submit-role` with cluster-wide permissions:

| Resource | Verbs |
|---|---|
| `sparkapplications` (sparkoperator.k8s.io) | create, get, list, watch, update, patch, delete |
| `pods`, `pods/log` | get, list, watch |
| `services` | get, list |
| `configmaps` | get, list |

**`spark-submit-clusterrolebinding.yaml`** — Binds `spark-submit-role` to:

| ServiceAccount | Namespace | Purpose |
|---|---|---|
| `openhouse-spark-operator-spark` | `default` | Spark Operator submitter |
| `openhouse-airflow-worker` | `default` | Airflow Worker (SparkKubernetesOperator) |
| `openhouse-airflow-triggerer` | `default` | Airflow Triggerer (async lifecycle monitoring) |

#### Why two sets of RBAC?

- `airflow-spark-rbac.yaml` (Role/RoleBinding) grants namespace-scoped access in `stock-anomaly-detection` where Spark jobs actually run.
- `spark-submit-clusterrole.yaml` + `spark-submit-clusterrolebinding.yaml` (ClusterRole/ClusterRoleBinding) grant cluster-wide access so Airflow workers in the `default` namespace can also manage SparkApplications across namespaces.
