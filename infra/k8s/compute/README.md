# Compute Infrastructure

This directory manages compute resources for the data platform on Kubernetes: **Spark Operator** (runs batch/streaming jobs) and **Trino** (runs SQL queries on the Iceberg lakehouse).

## Directory Structure

### `/config`

- `spark.yaml` — Helm values for Spark Operator (overrides default chart values)
- `spark-serviceaccount-rbac.yaml` — ServiceAccount + RBAC for Spark applications
- `trino.yaml` — Helm values for Trino (overrides default chart values)

### `/debug`

Debug resources for troubleshooting:

- `spark_controller.yaml` — debug pod for the Spark Operator controller
- `gravitino-iceberg-debug-pod.yaml` — debug pod for Gravitino Iceberg REST

### `/scripts`

Automation scripts for managing compute infrastructure.

### `/test_template`

Template output for testing Helm chart rendering without deploying:

- `spark_operator_template.yaml` — rendered Spark Operator manifests

---

## Installing Spark Operator

### 1. Apply ServiceAccount and RBAC

Before running any SparkApplication, you must create the `spark` ServiceAccount and RBAC permissions in the `stock-anomaly-detection` namespace:

```bash
kubectl apply -f config/spark-serviceaccount-rbac.yaml
```

This file creates:

- **ServiceAccount** `spark` in the `stock-anomaly-detection` namespace
- **Role** `spark-app-role` with permissions to manage pods, services, configmaps, PVCs, events, and read secrets
- **RoleBinding** `spark-app-rolebinding` binding the role to the service account

> **Note:** Every SparkApplication manifest declares `serviceAccount: spark` in the driver spec. If you skip this step, the driver pod will fail with a `forbidden` error.

### 2. Install Spark Operator

```bash
./scripts/install_spark_operators.sh
```

This command runs:

```bash
helm upgrade --install openhouse-spark-operator helm/spark-operator -f config/spark.yaml
```

**Main configuration in `config/spark.yaml`:**

- **Controller**: 1 replica, 10 workers, info-level logging
- **Webhook**: enabled, port 9443, timeout 10s
- **Leader Election**: enabled for controller and webhook
- **Prometheus Metrics**: enabled on port 8080, endpoint `/metrics`
- **Cert Manager**: disabled (uses self-signed certificates)

#### What's different from the chart's default values?

`config/spark.yaml` starts as a full copy of the `spark-operator/spark-operator` Helm chart's default `values.yaml`. Only these values were intentionally changed:

| Setting                | Chart default       | This project                                                            | Why it was changed                                                                                              |
| ---------------------- | ------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `controller.workers`   | `10`                | `6`                                                                     | Lower concurrency, so multiple `spark-submit` processes running at once don't starve the node's CPU.            |
| `controller.resources` | not set (unlimited) | requests `200m` CPU / `512Mi` memory, limits `2000m` CPU / `1Gi` memory | Give the controller pod a guaranteed minimum and a hard ceiling, instead of letting it use unlimited resources. |
| `spark.jobNamespaces`  | `["default"]`       | `["stock-anomaly-detection"]`                                           | Only watch for `SparkApplication` resources in this project's own namespace.                                    |

> Any other line that differs from the _newest_ chart default (things like `featureGates`, `hostUsers`, `jobNamespaceSelector`) is **not** an intentional change — it simply means the upstream chart added new fields after `spark.yaml` was written. Only the 3 rows above were changed on purpose.

### 3. Uninstall Spark Operator

```bash
./scripts/uninstall_spark_operators.sh
```

---

## Installing Trino

Trino is the SQL query engine used to read the Iceberg lakehouse (Bronze / Silver / Gold layers).

### Before you start

Trino needs these components already running in the `stock-anomaly-detection` namespace — install them first (see `../storage/` and `../orchestration/`):

| Component     | Why Trino needs it                                                                                  |
| ------------- | --------------------------------------------------------------------------------------------------- |
| **MinIO**     | Object storage that actually holds the Iceberg data files (Parquet).                                |
| **Keycloak**  | Issues the OAuth2 login tokens every Trino query uses to authenticate.                              |
| **Gravitino** | The Iceberg REST catalog service. Trino sends every table lookup here (`openhouse-gravitino:9001`). |

### 1. Install Trino

```bash
./scripts/install_trino.sh
```

This one command does two things, in order:

1. **Creates a PersistentVolumeClaim** named `catalogs-pvc` (1Gi). The Trino coordinator mounts this at `/etc/trino/dynamic-catalog` so it has a place to store catalogs that get added while Trino is running (not just the ones baked into `config/trino.yaml`).
2. **Installs Trino via Helm**:
   ```bash
   helm upgrade --install --namespace stock-anomaly-detection openhouse-trino trino/trino -f config/trino.yaml
   ```

### 2. Preview the rendered manifests (optional)

```bash
./scripts/template_trino.sh
```

This renders what Helm _would_ deploy into `test_template/`, without actually deploying anything. Useful for checking a config change before applying it.

### 3. Uninstall Trino

```bash
./scripts/uninstall_trino.sh
```

### Checking after install

```bash
# Trino coordinator + worker pods should be Running
kubectl get pods -n stock-anomaly-detection -l app.kubernetes.io/name=trino

# The Trino service should exist
kubectl get svc -n stock-anomaly-detection openhouse-trino

# The dynamic-catalog PVC should be Bound
kubectl get pvc -n stock-anomaly-detection catalogs-pvc
```

### What's different from the chart's default values?

`config/trino.yaml` starts as a full copy of the `trino/trino` Helm chart's default `values.yaml`. Only these values were intentionally changed:

| Setting                                                           | Chart default   | This project                                                                                                                                    | Why it was changed                                                                                        |
| ----------------------------------------------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `server.workers`                                                  | `2`             | `1`                                                                                                                                             | Small/dev-sized cluster — one worker pod is enough.                                                       |
| `server.config.authenticationType` + `additionalConfigProperties` | empty (no auth) | OAuth2 through Keycloak — issuer, auth/token/jwks URLs, client id + secret, internal shared secret                                              | Every query must log in through the Keycloak realm `iceberg` before Trino will run it.                    |
| `catalogs.bronze`, `catalogs.silver`, `catalogs.gold`             | not present     | 3 Iceberg REST catalogs, each pointing at Gravitino (`openhouse-gravitino:9001`) with its own OAuth2 credential and MinIO/S3 connection details | This is the actual wiring that lets Trino query the lakehouse — one catalog per Bronze/Silver/Gold layer. |
| `ingress`                                                         | disabled        | enabled, class `nginx`, host `openhouse.trino.test`                                                                                             | Exposes the Trino UI and API outside the cluster.                                                         |

> Same as Spark above: any other line that differs from the _newest_ chart default (fields like `gateway`, `headerAuthenticator`, `hostUsers`) is just the upstream chart adding new options after `trino.yaml` was written — not an intentional change. Only the 4 rows above were changed on purpose.

> ⚠️ **Security note**: `config/trino.yaml` currently stores the Keycloak client secret and MinIO access keys as **plain text**. Treat this file as sensitive (do not share it publicly) and consider moving these values into a Kubernetes `Secret` later.

---

## Scripts Guide

| Script                         | Description                                                        |
| ------------------------------ | ------------------------------------------------------------------ |
| `install_spark_operators.sh`   | Install/upgrade Spark Operator via Helm                            |
| `uninstall_spark_operators.sh` | Remove the Spark Operator Helm release                             |
| `template_spark.sh`            | Render manifests into `test_template/` for preview                 |
| `install_trino.sh`             | Create the `catalogs-pvc` PVC, then install/upgrade Trino via Helm |
| `uninstall_trino.sh`           | Remove the Trino Helm release                                      |
| `template_trino.sh`            | Render Trino manifests into `test_template/` for preview           |

---

## Post-install Verification

```bash
# Check that Spark Operator is running
kubectl get pods -n stock-anomaly-detection

# Check that the ServiceAccount was created
kubectl get serviceaccount spark -n stock-anomaly-detection

# Check RBAC
kubectl get role,rolebinding -n stock-anomaly-detection
```
