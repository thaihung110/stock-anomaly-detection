# Kubernetes Infrastructure

This directory contains all Kubernetes deployment configurations for the data platform components.

## Directory Structure

```
infra/k8s/
├── storage/           # Storage and data management layer
├── orchestration/     # Workflow and messaging layer
├── ingestion/         # Data ingestion services
└── compute/           # Compute and deployment automation
```

## Components Overview

### Storage Layer (`storage/`)

**MinIO** - Object Storage

- S3-compatible storage for data lakes
- Buckets: `raw`, `bronze`, `silver`, `gold`
- Credentials: `admin` / `admin123`
- Deployment: Helm chart (Bitnami)

**PostgreSQL** - Relational Database

- Metadata storage for platform components
- Databases: `keycloak`, `catalog`, `openfga`, `source_api`
- Credentials: `postgres` / `admin`
- Deployment: Helm chart (Bitnami)

**Keycloak** - Identity & Access Management

- OAuth2/OIDC authentication provider
- Realm: `iceberg`
- Clients: `lakekeeper` (public), `spark` (confidential)
- HTTPS required
- Deployment: Helm chart (Bitnami)

**OpenFGA** - Authorization

- Fine-grained access control
- Integrates with Lakekeeper
- Deployment: Official Helm chart

**Lakekeeper** - Iceberg Catalog

- Apache Iceberg REST catalog service
- Manages table metadata and schemas
- Warehouses: `bronze`, `silver`, `gold`
- HTTPS required
- Deployment: Official Helm chart

**Documentation**: [storage/README.md](storage/README.md)

---

### Orchestration Layer (`orchestration/`)

**Apache Kafka** - Message Broker

- Event streaming platform
- KRaft mode (no Zookeeper)
- SASL authentication enabled
- Topics: `csv-ingestion`
- Single-node configuration for development
- Deployment: Helm chart (Bitnami Legacy)

**Kafka UI** - Management Interface

- Web UI for Kafka cluster
- Topic monitoring and message inspection
- SASL connection to Kafka
- Deployment: Helm chart (Provectus)

**Apache NiFi** - Data Flow Automation

- Visual data flow designer
- Processors: ConsumeKafka, InvokeHTTP, ConvertRecord, PutS3Object
- Flow: CSV chunks → Parquet → MinIO
- Single-user authentication
- Deployment: Helm chart

**Apache Airflow** - Workflow Orchestration

- DAG-based workflow management
- Git-Sync for DAG deployment
- KubernetesPodOperator for Spark jobs
- Executor: CeleryExecutor
- Deployment: Official Helm chart

**RBAC** - Role-Based Access Control

- ClusterRole: `spark-submit-role`
- ClusterRoleBinding: `spark-submit-binding`
- Grants Airflow permission to manage Spark jobs

**Documentation**: [orchestration/README.md](orchestration/README.md)

---

### Ingestion Layer (`ingestion/`)

**Source API** - CSV Upload Service

- FastAPI REST API
- Endpoints: `/api/v1/upload/csv`, `/api/v1/health`
- Chunks large CSV files
- Publishes metadata to Kafka
- Persistent storage: 10Gi PVC
- Deployment: Custom Kubernetes manifests

**Documentation**: [ingestion/README.md](ingestion/README.md)

---

### Compute Layer (`compute/`)

**Spark Operator** - Spark Job Management

- Kubeflow Spark Operator
- Manages SparkApplication CRDs
- Automatic driver/executor pod lifecycle
- Service account: `openhouse-spark-operator-spark`
- Deployment: Helm chart

**Argo CD** - GitOps Continuous Deployment

- Declarative GitOps deployment
- Manages Kubernetes resources
- Credentials: `admin` / `admin123`
- Deployment: Helm chart

**Spark Applications**

- `taxi-data-ingestion`: MinIO → Iceberg pipeline
- Requires Lakekeeper warehouse permissions
- OAuth2 authentication with Keycloak

**Documentation**: [compute/README.md](compute/README.md)

---

## Deployment Guide

### Prerequisites

- Kubernetes cluster (v1.28+)
- kubectl configured
- Helm 3.x installed
- Ingress controller (nginx)
- StorageClass available

### Installation Order

**1. Storage Layer** (Foundation)

```bash
cd storage

# Database
./scripts/install_postgresql.sh

# Object storage
./scripts/install_minio.sh

# Authentication
./scripts/create_secret_keycloak_tls.sh
./scripts/install_keycloak.sh

# Authorization
./scripts/install_openfga.sh

# Catalog
./scripts/create_secret_lakekeeper_tls.sh
./scripts/install_lakekeeper.sh
```

**2. Orchestration Layer** (Messaging & Workflows)

```bash
cd orchestration

# Message broker
./scripts/install_kafka.sh
./scripts/install_kafka_ui.sh

# Data flow
./scripts/install_nifi.sh

# Workflow orchestration
./scripts/install_airflow.sh

# RBAC for Spark jobs (REQUIRED before running Airflow Spark DAGs)
kubectl apply -f rbac/spark-submit-clusterrole.yaml
kubectl apply -f rbac/spark-submit-clusterrolebinding.yaml
```

**3. Ingestion Layer** (Data Entry Points)

```bash
cd ingestion

# CSV upload API
./scripts/install_source_api.sh

# Create Kafka topic
./scripts/create_kafka_topics_sasl.sh
```

**4. Compute Layer** (Processing)

```bash
cd compute

# Spark operator
./scripts/install_spark_operators.sh

# GitOps (optional)
./scripts/install_argocd.sh
```

---

## Post-Deployment Configuration

### 1. Keycloak Setup

Access Keycloak UI and configure:

1. Create realm `iceberg`
2. Create client `lakekeeper` (public)
   - Add client scope `lakekeeper` with audience mapper
3. Create client `spark` (confidential)
   - Get client secret from Credentials tab
   - Add client scope `sign`
4. Create user `admin/admin`

**Guide**: [storage/README.md#keycloak-configuration](storage/README.md#keycloak-configuration)

### 2. Lakekeeper Setup

Access Lakekeeper UI and configure:

1. Login with `admin/admin`
2. Perform bootstrap
3. Create warehouse connecting to MinIO
4. Grant permissions to `service-account-spark`:
   - Copy service account ID
   - Grant ownership role on warehouse

**Guide**: [compute/applications/spark/README.md#grant-lakekeeper-access](compute/applications/spark/README.md#grant-lakekeeper-access-first-run-only)

### 3. NiFi Flow Setup

Access NiFi UI and create processor group:

1. ConsumeKafka_2_6 (with SASL authentication)
2. EvaluateJsonPath (extract metadata)
3. InvokeHTTP (download CSV)
4. ConvertRecord (CSV → Parquet)
5. UpdateAttribute (set S3 path)
6. PutS3Object (upload to MinIO)

**Guide**: [orchestration/README.md#nifi-processor-group](orchestration/README.md#nifi-processor-group-csv-to-parquet-ingestion)

### 4. MinIO Bucket Creation

Create `raw` bucket in MinIO Console:

```bash
# Port-forward MinIO
kubectl port-forward -n default svc/minio 9001:9001

# Access Console: http://localhost:9001
# Login: admin / admin123
# Create bucket: raw
```

### 5. Airflow Git-Sync (Optional)

Configure Airflow to sync DAGs from private GitHub repository:

1. Create private GitHub repo for DAGs
2. Generate SSH key pair
3. Add deploy key to GitHub
4. Update `orchestration/config/airflow.yaml` with SSH secret
5. Upgrade Airflow Helm release

**Guide**: [../airflow/README.md#git-sync-setup](../airflow/README.md#-git-sync-setup-for-dag-deployment)

---

## Access URLs

Configure `/etc/hosts` or DNS with ingress IP:

```
<ingress-ip> openhouse.airflow.test
<ingress-ip> openhouse.kafka-ui.test
<ingress-ip> openhouse.nifi.test
<ingress-ip> openhouse.keycloak.test
<ingress-ip> openhouse.lakekeeper.test
```

| Service    | URL                               | Credentials             |
| ---------- | --------------------------------- | ----------------------- |
| Airflow    | https://openhouse.airflow.test    | admin / admin           |
| Kafka UI   | https://openhouse.kafka-ui.test   | No auth                 |
| NiFi       | https://openhouse.nifi.test       | admin / adminadminadmin |
| Keycloak   | https://openhouse.keycloak.test   | admin / admin           |
| Lakekeeper | https://openhouse.lakekeeper.test | admin / admin           |
| Argo CD    | Port-forward 8080                 | admin / (get password)  |

---

## Configuration Files

### Storage Layer

- `storage/config/postgresql.yaml` - PostgreSQL configuration
- `storage/config/keycloak.yaml` - Keycloak settings
- `storage/config/lakekeeper.yaml` - Lakekeeper catalog config

### Orchestration Layer

- `orchestration/config/kafka.yaml` - Kafka broker settings
- `orchestration/config/kafka-ui.yaml` - Kafka UI configuration
- `orchestration/config/nifi.yaml` - NiFi settings
- `orchestration/config/airflow.yaml` - Airflow configuration

### Ingestion Layer

- `ingestion/application/source-api.yaml` - Source API deployment

### Compute Layer

- `compute/config/spark.yaml` - Spark Operator configuration
- `compute/config/argo.yaml` - Argo CD settings

---

## Uninstallation

Reverse order of installation:

```bash
# Compute
cd compute
./scripts/uninstall_spark_operators.sh
./scripts/uninstall_argocd.sh

# Ingestion
cd ingestion
./scripts/uninstall_source_api.sh

# Orchestration
cd orchestration
./scripts/uninstall_airflow.sh
./scripts/uninstall_nifi.sh
./scripts/uninstall_kafka_ui.sh
./scripts/uninstall_kafka.sh

# Storage
cd storage
./scripts/uninstall_lakekeeper.sh
./scripts/uninstall_openfga.sh
./scripts/uninstall_keycloak.sh
./scripts/uninstall_minio.sh
./scripts/uninstall_postgresql.sh
```

---

## Troubleshooting

### Check Component Status

```bash
# All pods
kubectl get pods -A

# Specific component
kubectl get pods -l app=<component-name>

# Logs
kubectl logs -f <pod-name> -n <namespace>
```

### Common Issues

**Pods in CrashLoopBackOff**:

- Check logs: `kubectl logs <pod-name>`
- Check events: `kubectl describe pod <pod-name>`
- Verify dependencies are running

**PVC not binding**:

- Check StorageClass exists: `kubectl get storageclass`
- Check PV availability: `kubectl get pv`

**Ingress not working**:

- Verify ingress controller: `kubectl get pods -n ingress-nginx`
- Check ingress resources: `kubectl get ingress -A`

**SASL authentication failures**:

- Verify credentials match Kafka configuration
- Check security protocol: `SASL_PLAINTEXT`

### Component-Specific Guides

- Storage: [storage/README.md](storage/README.md)
- Orchestration: [orchestration/README.md](orchestration/README.md)
- Ingestion: [ingestion/README.md](ingestion/README.md)
- Compute: [compute/README.md](compute/README.md)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster                        │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Storage    │  │Orchestration │  │  Ingestion   │      │
│  │              │  │              │  │              │      │
│  │  PostgreSQL  │  │    Kafka     │  │  Source API  │      │
│  │    MinIO     │  │   Kafka UI   │  │              │      │
│  │  Keycloak    │  │    NiFi      │  └──────────────┘      │
│  │  OpenFGA     │  │   Airflow    │                        │
│  │ Lakekeeper   │  │              │  ┌──────────────┐      │
│  │              │  └──────────────┘  │   Compute    │      │
│  └──────────────┘                    │              │      │
│                                      │Spark Operator│      │
│                                      │   Argo CD    │      │
│                                      │              │      │
│                                      └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

---

## Resource Requirements

### Minimum (Development)

- **CPU**: 8 cores
- **Memory**: 16 GB RAM
- **Storage**: 50 GB

### Recommended (Production)

- **CPU**: 16+ cores
- **Memory**: 32+ GB RAM
- **Storage**: 200+ GB
- **Nodes**: 3+ worker nodes

---

## Security Considerations

- **HTTPS**: Keycloak and Lakekeeper require HTTPS
- **SASL**: Kafka uses SASL_PLAINTEXT authentication
- **OAuth2**: Spark jobs authenticate via Keycloak
- **RBAC**: Kubernetes RBAC for service accounts
- **Secrets**: Use Kubernetes Secrets for sensitive data
- **Network Policies**: Consider implementing for production

---

## Monitoring & Observability

- **Kafka UI**: Monitor Kafka topics and consumer groups
- **Airflow UI**: DAG execution monitoring
- **NiFi UI**: Data flow monitoring
- **Spark UI**: Job execution metrics
- **Prometheus**: Metrics collection (optional)
- **Grafana**: Visualization (optional)
