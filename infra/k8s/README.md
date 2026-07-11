# Kubernetes Infrastructure

This directory contains all Kubernetes deployment configurations for the data platform components.

## Directory Structure

```
infra/k8s/
├── storage/           # Storage and data management layer
├── orchestration/     # Workflow and messaging layer
└── compute/           # Compute layer: Spark Operator + Trino
```

## Components Overview

### Storage Layer (`storage/`)

**MinIO** - Object Storage

- S3-compatible storage for the Iceberg lakehouse (Bronze/Silver/Gold layers)
- Credentials: `admin` / `admin123`
- Deployment: Helm chart (Bitnami)

**PostgreSQL** - Relational Database

- Metadata storage for platform components
- Databases: `keycloak`, `gravitino_db`, `iceberg_catalog_db`, `stock_anomaly`
- Credentials: `postgres` / `admin`
- Deployment: Helm chart (Bitnami)

**Keycloak** - Identity & Access Management

- OAuth2/OIDC authentication provider
- Realm: `iceberg`
- Clients: `gravitino` (public, Web UI login), `spark` (confidential, machine-to-machine)
- HTTPS required
- Deployment: Helm chart (Bitnami)

**Gravitino** - Iceberg Catalog

- Apache Iceberg REST catalog service (metadata + JDBC backend on PostgreSQL, data on MinIO)
- Metalake: `stock_metalake`
- OAuth2-protected via Keycloak
- Deployment: local Helm chart (`storage/helm/gravitino`)

**Documentation**: [storage/README.md](storage/README.md)

---

### Orchestration Layer (`orchestration/`)

**Apache Kafka** - Message Broker

- Event streaming platform
- KRaft mode (no Zookeeper)
- Listeners: `PLAINTEXT` (see `config/kafka.yaml`)
- Single-node configuration for development
- Deployment: Helm chart (Bitnami)

**Kafka UI** - Management Interface

- Web UI for the Kafka cluster
- Topic monitoring and message inspection
- Deployment: Helm chart (Provectus)

**Apache Airflow** - Workflow Orchestration

- DAG-based workflow management, version `3.0.2`
- Git-Sync (SSH) pulls DAGs from `thaihung110/airflow-dags`
- SparkKubernetesOperator submits jobs to Spark Operator
- Executor: CeleryExecutor
- Deployment: Official Helm chart

**RBAC** - Role-Based Access Control

- `airflow-spark-rbac.yaml`: namespace-scoped Role/RoleBinding in `stock-anomaly-detection`
- `spark-submit-clusterrole.yaml` + `spark-submit-clusterrolebinding.yaml`: cluster-wide access for Airflow workers/triggerer and the Spark Operator service account
- Grants Airflow permission to create/manage `SparkApplication` resources

**Documentation**: [orchestration/README.md](orchestration/README.md)

---

### Compute Layer (`compute/`)

**Spark Operator** - Spark Job Management

- Kubeflow Spark Operator
- Manages `SparkApplication` CRDs, automatic driver/executor pod lifecycle
- Deployment: Helm chart

**Trino** - SQL Query Engine

- Queries the Iceberg lakehouse (Bronze/Silver/Gold) via Gravitino's REST catalog
- OAuth2-protected via Keycloak
- Deployment: Helm chart

**Documentation**: [compute/README.md](compute/README.md)

---

## Deployment Guide

### Prerequisites

- A Kubernetes cluster — this project runs on **kubeadm** (not a managed cloud cluster, not Minikube)
- kubectl configured against that cluster
- Helm 3.x installed, with the `bitnami`, `kafka-ui`, `apache-airflow`, `trino`, and `spark-operator` repos added
- StorageClass and Ingress controller are **not** assumed to pre-exist — Step 0 below sets both up from scratch

### Installation Order

**0. Bootstrap: StorageClass & Ingress-NGINX**

Every other layer depends on both of these existing first.

```bash
cd storage

# StorageClass `hostpath` with reclaimPolicy: Retain — every PVC in this project uses it
./scripts/set_storageclass_retain.sh

# Ingress-NGINX controller (resets any existing install, then applies the official manifest)
./scripts/ingress.sh
```

**Guide**: [storage/README.md#prerequisites-storageclass--ingress-nginx](storage/README.md#prerequisites-storageclass--ingress-nginx)

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

# Iceberg catalog
./scripts/create_secret_gravitino_tls_ingress.sh
./scripts/create_secret_gravitino_tls.sh
./scripts/install_gravitino.sh
```

**2. Orchestration Layer** (Messaging & Workflows)

```bash
cd orchestration

# Message broker
./scripts/install_kafka.sh
./scripts/install_kafka_ui.sh

# Create this project's Kafka topics (8 topics, see orchestration/README.md)
./scripts/create_kafka_topics_plaintext.sh

# Workflow orchestration
./scripts/install_airflow.sh

# RBAC for Spark jobs (REQUIRED before running Airflow Spark DAGs)
kubectl apply -f rbac/airflow-spark-rbac.yaml
kubectl apply -f rbac/spark-submit-clusterrole.yaml
kubectl apply -f rbac/spark-submit-clusterrolebinding.yaml
```

**3. Compute Layer** (Processing + Query)

```bash
cd compute

# ServiceAccount + RBAC for Spark applications
kubectl apply -f config/spark-serviceaccount-rbac.yaml

# Spark operator
./scripts/install_spark_operators.sh

# Trino (SQL query engine on the lakehouse)
./scripts/install_trino.sh
```

---

## Post-Deployment Configuration

### 1. Keycloak Setup

Access Keycloak UI and configure:

1. Create realm `iceberg`
2. Create client `gravitino` (public) — Authorization Code + PKCE for the Gravitino Web UI
3. Create client `spark` (confidential) — Client Credentials flow for Spark/Trino batch jobs
   - Get client secret from the Credentials tab
   - Add client scope `sign` and `gravitino` (see storage README for the audience-mapper details)
4. Create user `admin` / `admin`

**Guide**: [storage/README.md#keycloak-configuration](storage/README.md#keycloak-configuration)

### 2. Gravitino Setup

Access the Gravitino UI and configure:

1. Create metalake `stock_metalake` (must exist before any Spark/Trino job runs)
2. Create **3 Iceberg catalogs** — `bronze`, `silver`, `gold` (JDBC backend on `iceberg_catalog_db`, storage on the matching MinIO bucket)

> A script alternative exists (`storage/scripts/setup_gravitino_warehouses.sh`), but it only creates the `bronze` and `silver` catalogs — `gold` still has to be created manually via the UI either way.

**Guide**: [storage/README.md#4-setting-up-the-metalake-and-warehouses-iceberg-catalogs](storage/README.md#4-setting-up-the-metalake-and-warehouses-iceberg-catalogs)

### 3. MinIO Bucket Creation

Create the lakehouse buckets in the MinIO Console:

```bash
# Port-forward MinIO
kubectl port-forward -n stock-anomaly-detection svc/openhouse-minio 9001:9001

# Access Console: http://localhost:9001
# Login: admin / admin123
# Create buckets: bronze, silver, gold
```

### 4. Airflow Git-Sync (Required)

Airflow's `worker`, `triggerer`, and `dag-processor` pods use git-sync over SSH to clone DAGs from `git@github.com:thaihung110/airflow-dags.git`. The SSH secret is **not created by Helm** — it must exist before `install_airflow.sh` runs, or those pods will stay stuck in `Init:0/2`.

**Guide**: [orchestration/README.md#prerequisite-ssh-secret-cho-git-sync-bắt-buộc-trước-khi-cài](orchestration/README.md#prerequisite-ssh-secret-cho-git-sync-bắt-buộc-trước-khi-cài)

---

## Access URLs

Configure `/etc/hosts` or DNS with the ingress IP:

```
<ingress-ip> openhouse.airflow.test
<ingress-ip> openhouse.kafka-ui.test
<ingress-ip> openhouse.keycloak.test
<ingress-ip> openhouse.gravitino.test
<ingress-ip> openhouse.trino.test
```

| Service   | URL                              | Credentials                         |
| --------- | -------------------------------- | ----------------------------------- |
| Airflow   | https://openhouse.airflow.test   | admin / admin                       |
| Kafka UI  | https://openhouse.kafka-ui.test  | No auth                             |
| Keycloak  | https://openhouse.keycloak.test  | admin / admin                       |
| Gravitino | https://openhouse.gravitino.test | admin / admin (via Keycloak login)  |
| Trino     | https://openhouse.trino.test     | via Keycloak OAuth2 (no fixed user) |

---

## Configuration Files

### Storage Layer

- `storage/config/postgresql.yaml` - PostgreSQL configuration
- `storage/config/keycloak.yaml` - Keycloak settings
- `storage/config/gravitino.yaml` - Gravitino catalog config
- `storage/config/minio.yaml` - MinIO configuration

### Orchestration Layer

- `orchestration/config/kafka.yaml` - Kafka broker settings
- `orchestration/config/kafka-ui.yaml` - Kafka UI configuration
- `orchestration/config/airflow.yaml` - Airflow configuration

### Compute Layer

- `compute/config/spark.yaml` - Spark Operator configuration
- `compute/config/trino.yaml` - Trino configuration

---

## Uninstallation

Reverse order of installation:

```bash
# Compute
cd compute
./scripts/uninstall_trino.sh
./scripts/uninstall_spark_operators.sh

# Orchestration
cd orchestration
./scripts/uninstall_airflow.sh
./scripts/uninstall_kafka_ui.sh
./scripts/uninstall_kafka.sh

# Storage
cd storage
./scripts/uninstall_gravitino.sh
./scripts/uninstall_keycloak.sh
./scripts/uninstall_minio.sh
./scripts/uninstall_postgresql.sh
```

> **PVs are not deleted along with these releases.** The `hostpath` StorageClass was created with `reclaimPolicy: Retain` (see Step 0 in the Deployment Guide), so the underlying PersistentVolumes survive `helm uninstall` and PVC deletion. Reinstalling a release with the same name will **not** automatically rebind its old PV — if you want a genuinely clean slate, manually delete the released PVs (`kubectl get pv`, filter by the old claim) after uninstalling.

---

## Troubleshooting

### Check Component Status

```bash
# All pods
kubectl get pods -n stock-anomaly-detection

# Specific component
kubectl get pods -n stock-anomaly-detection -l app=<component-name>

# Logs
kubectl logs -f <pod-name> -n stock-anomaly-detection
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

**Airflow git-sync pods stuck in `Init:0/2`**:

- The `airflow-ssh-secret` is missing — see [Airflow Git-Sync](#4-airflow-git-sync-required) above.

### Component-Specific Guides

- Storage: [storage/README.md](storage/README.md)
- Orchestration: [orchestration/README.md](orchestration/README.md)
- Compute: [compute/README.md](compute/README.md)

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster                        │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Storage    │  │Orchestration │  │   Compute    │        │
│  │              │  │              │  │              │        │
│  │  PostgreSQL  │  │    Kafka     │  │Spark Operator│        │
│  │    MinIO     │  │   Kafka UI   │  │    Trino     │        │
│  │  Keycloak    │  │   Airflow    │  │              │        │
│  │  Gravitino   │  │              │  │              │        │
│  │              │  │              │  │              │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
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

- **HTTPS**: Keycloak and Gravitino require HTTPS
- **OAuth2**: Spark and Trino jobs authenticate via Keycloak
- **RBAC**: Kubernetes RBAC for service accounts
- **Secrets**: Use Kubernetes Secrets for sensitive data
- **Network Policies**: Consider implementing for production

---

## Monitoring & Observability

- **Kafka UI**: Monitor Kafka topics and consumer groups
- **Airflow UI**: DAG execution monitoring
- **Spark UI**: Job execution metrics
- **Prometheus**: Metrics collection (optional)
- **Grafana**: Visualization (optional)
