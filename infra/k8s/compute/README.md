# Compute Infrastructure

This directory manages compute resources for the data platform, primarily **Spark Operator** and **Argo CD** deployments on Kubernetes.

## Configuration Files Comparison

### `config/spark.yaml` vs `helm/spark-operator/values.yaml`

Both files are **identical** - they contain the same Spark Operator Helm values configuration. The key difference is their **usage context**:

- **`config/spark.yaml`**: Custom configuration file used during deployment via `install_spark_operators.sh` to override default Helm chart values
- **`helm/spark-operator/values.yaml`**: Default values file included in the Spark Operator Helm chart

**Key configurations include:**

- **Controller**: 1 replica, 10 workers, info-level logging with console encoding
- **Webhook**: Enabled with 1 replica, port 9443, 10s timeout
- **Leader Election**: Enabled for both controller and webhook
- **Spark Jobs**: Allowed in `default` namespace only
- **Prometheus Metrics**: Enabled on port 8080 at `/metrics` endpoint
- **Batch Scheduler**: Disabled
- **Cert Manager**: Disabled (webhook uses self-signed certificates)

## Directory Structure

### `/applications`

Contains Spark application manifests organized by type. Currently includes `/spark/legacy` subfolder with `taxi-data-ingestion.yaml` for batch ingestion jobs.

### `/config`

Helm values configuration files for deployments:

- `argo.yaml`: Argo CD configuration
- `spark.yaml`: Spark Operator configuration (overrides default Helm values)

### `/debug`

Debug resources for troubleshooting deployments. Contains `spark_controller.yaml` for debugging Spark Operator controller issues.

### `/helm`

Helm chart repositories:

- `argo-cd/`: Argo CD Helm chart
- `spark-operator/`: Spark Operator Helm chart

### `/scripts`

Automation scripts for managing compute infrastructure (detailed below).

### `/test_template`

Template output directory for testing Helm chart rendering without deployment:

- `argocd_template.yaml`: Generated Argo CD manifests
- `spark_operator_template.yaml`: Generated Spark Operator manifests

## Scripts Guide

### Installation Scripts

**`install_argocd.sh`**

- Installs/upgrades Argo CD using Helm with custom configuration
- Command: `helm upgrade --install openhouse-argocd helm/argo-cd -f config/argo.yaml`

**`install_spark_operators.sh`**

- Installs/upgrades Spark Operator using Helm with custom configuration
- Command: `helm upgrade --install openhouse-spark-operator helm/spark-operator -f config/spark.yaml`

### Uninstallation Scripts

**`uninstall_argocd.sh`**

- Removes Argo CD Helm release
- Command: `helm uninstall openhouse-argocd`

**`uninstall_spark_operators.sh`**

- Removes Spark Operator Helm release
- Command: `helm uninstall openhouse-spark-operator`

### Template Scripts

**`template_argocd.sh`**

- Renders Argo CD manifests to `test_template/argocd_template.yaml` for preview without deploying

**`template_spark.sh`**

- Renders Spark Operator manifests to `test_template/spark_operator_template.yaml` for preview without deploying

### Spark Job Management Scripts

**`create_spark_manifests_configmap.sh`**

- Creates/updates ConfigMap `spark-manifests` containing `taxi-data-ingestion.yaml`
- Mounted into Airflow pods for DAG access to SparkApplication specs
- Namespace: `default`

### Streaming Spark Job Scripts

**`start_load_crypto_bronze.sh`**

- Deploys crypto bronze data ingestion SparkApplication
- Checks for existing jobs (prompts for deletion if found)
- Displays status and useful kubectl commands for monitoring driver/executor logs

**`stop_load_crypto_bronze.sh`**

- Deletes crypto bronze data ingestion SparkApplication
- Shows current job status before deletion
- Confirms deletion for running/submitted jobs
- Waits for pod cleanup
