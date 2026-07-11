# Orchestration Infrastructure

This directory manages the messaging and workflow layer on Kubernetes: **Kafka** (event streaming), **Kafka UI** (Kafka management console), and **Airflow** (DAG-based batch orchestration).

## Directory Structure

### `/config`

- `kafka.yaml` — Helm values for Kafka (overrides default chart values)
- `kafka-ui.yaml` — Helm values for Kafka UI (overrides default chart values)
- `airflow.yaml` — Helm values for Airflow with full Keycloak-style auth (not currently used by `install_airflow.sh` — see the Airflow section below)
- `airflow-no-auth.yaml` — Helm values for Airflow with simplified single-admin auth (this is the file `install_airflow.sh` actually deploys)

### `/rbac`

- `airflow-spark-rbac.yaml` — namespace-scoped Role/RoleBinding letting Airflow manage `SparkApplication` resources
- `spark-submit-clusterrole.yaml` / `spark-submit-clusterrolebinding.yaml` — cluster-wide RBAC for the same purpose

### `/scripts`

Automation scripts for installing, uninstalling, and previewing each component.

### `/test_template`

Template output for testing Helm chart rendering without deploying:

- `kafka-ui_template.yaml` — rendered Kafka UI manifests
- `airflow_template.yaml` — rendered Airflow manifests

---

## Installing Kafka

### 1. Install Kafka

```bash
./scripts/install_kafka.sh
```

This runs:

```bash
helm upgrade --install --namespace stock-anomaly-detection openhouse-kafka bitnami/kafka -f config/kafka.yaml
```

**Main configuration in `config/kafka.yaml`:**

- **Mode**: KRaft (no Zookeeper), 1 combined controller+broker node
- **Listeners**: `PLAINTEXT` on all 4 ports — client (`9092`), controller (`9093`), inter-broker (`9094`), external (`9095`)
- **Persistence**: enabled for both data and logs, on the `hostpath` StorageClass

> **Single-node caveat**: with only one broker, Kafka's internal topics (`__consumer_offsets`, the transaction log) can't satisfy their default replication factor of 3. `config/kafka.yaml` sets `offsets.topic.replication.factor=1`, `transaction.state.log.replication.factor=1`, and `transaction.state.log.min.isr=1` to work around this — without them the broker crashes or consumers fail to find a leader.

#### What's different from the chart's default values?

`config/kafka.yaml` starts as a full copy of the `bitnami/kafka` chart's default `values.yaml`. Only these values were intentionally changed:

| Setting                                                  | Chart default          | This project                                                                        | Why it was changed                                                                         |
| -------------------------------------------------------- | ---------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `controller.replicaCount`                                | `3`                    | `1`                                                                                 | Single-node development cluster instead of a 3-node quorum.                                |
| `defaultStorageClass` / every `persistence.storageClass` | `""` (cluster default) | `"hostpath"`                                                                        | Pin all Kafka PVCs to this dev cluster's `hostpath` StorageClass.                          |
| `listeners.*.protocol` (all 4 listeners)                 | `SASL_PLAINTEXT`       | `PLAINTEXT`                                                                         | Drop SASL authentication to simplify local connections — see the single-node caveat above. |
| `extraEnvVars`                                           | not set                | `ALLOW_PLAINTEXT_LISTENER=yes` + the 3 replication-factor overrides described above | Required for `PLAINTEXT` listeners and single-node operation to actually work.             |

### 2. Preview the rendered manifests (optional)

```bash
./scripts/template_kafka.sh
```

### 3. Creating Kafka Topics

Once Kafka is running, create the project's topics with:

```bash
./scripts/create_kafka_topics_plaintext.sh
```

With no arguments, this creates the full V3.3 topic set used by every service in the `stock-anomaly-detection` namespace:

| Topic              | Producer                   | Consumer                                                     |
| ------------------ | -------------------------- | ------------------------------------------------------------ |
| `raw.stock.quotes` | yfinance quotes producer   | Rule Engine                                                  |
| `raw.stock.trades` | Finnhub trades producer    | `trades-ohlcv-stream` (Spark)                                |
| `raw.stock.news`   | Finnhub news producer      | `news-ingest-stream` (Spark)                                 |
| `alerts.raw`       | Rule Engine (system rules) | Alert Service (direct — LLM Agent not yet deployed, ADR-002) |
| `alerts.user`      | Rule Engine (custom rules) | Alert Service (custom delivery, ADR-001)                     |
| `alerts.failed`    | Alert Service (DLQ)        | Operator replay tooling                                      |
| `alerts.confirmed` | LLM Agent (future)         | Alert Service                                                |
| `alerts.followup`  | LLM Agent (future)         | Alert Service (Stage C follow-up)                            |

To create or update a single topic instead of the full set:

```bash
./scripts/create_kafka_topics_plaintext.sh <topic_name> [partitions] [replication_factor]
```

Defaults can be overridden via environment variables before running the script:

| Variable                   | Default                        | Purpose                                                |
| -------------------------- | ------------------------------ | ------------------------------------------------------ |
| `NAMESPACE`                | `stock-anomaly-detection`      | Namespace the Kafka pod runs in                        |
| `KAFKA_POD`                | `openhouse-kafka-controller-0` | Pod to `kubectl exec` into                             |
| `BOOTSTRAP_SERVER`         | `localhost:9092`               | Bootstrap server, as seen from inside the pod          |
| `TOPIC_PARTITIONS_DEFAULT` | `3`                            | Partitions for topics created in full-set mode         |
| `REPLICATION_FACTOR`       | `1`                            | Replication factor for topics created in full-set mode |
| `RETENTION_MS_DEFAULT`     | `604800000` (7 days)           | Retention for topics created in full-set mode          |

### 4. Uninstall Kafka

```bash
./scripts/uninstall_kafka.sh
```

### Checking after install

```bash
# Kafka pods should be Running
kubectl get pods -n stock-anomaly-detection -l app.kubernetes.io/instance=openhouse-kafka

# List topics
kubectl exec -n stock-anomaly-detection openhouse-kafka-controller-0 -- kafka-topics.sh \
  --list --bootstrap-server localhost:9092
```

---

## Installing Kafka UI

Kafka UI is a web console for browsing topics, partitions, consumer groups, and messages on the Kafka cluster above.

### 1. Install Kafka UI

```bash
./scripts/install_kafka_ui.sh
```

This runs:

```bash
helm upgrade --install --namespace stock-anomaly-detection openhouse-kafka-ui kafka-ui/kafka-ui -f config/kafka-ui.yaml
```

**Main configuration in `config/kafka-ui.yaml`:**

- Connects to cluster `openhouse-kafka` at `openhouse-kafka:9092` (`PLAINTEXT`)
- Authentication: disabled
- Ingress: enabled, class `nginx`, host `openhouse.kafka-ui.test`

#### What's different from the chart's default values?

`config/kafka-ui.yaml` starts as a full copy of the `kafka-ui/kafka-ui` chart's default `values.yaml`. Only these values were intentionally changed:

| Setting                                  | Chart default         | This project                                                          | Why it was changed                             |
| ---------------------------------------- | --------------------- | --------------------------------------------------------------------- | ---------------------------------------------- |
| `yamlApplicationConfig.kafka.clusters`   | empty                 | one cluster: `openhouse-kafka` at `openhouse-kafka:9092`, `PLAINTEXT` | Point Kafka UI at this project's Kafka broker. |
| `yamlApplicationConfig.auth.type`        | not set               | `disabled`                                                            | No login required for local/dev use.           |
| `ingress.enabled` / `className` / `host` | `false` / `""` / `""` | `true` / `"nginx"` / `"openhouse.kafka-ui.test"`                      | Expose the UI outside the cluster.             |

### 2. Preview the rendered manifests (optional)

```bash
./scripts/template_kafka_ui.sh
```

### 3. Uninstall Kafka UI

```bash
./scripts/uninstall_kafka_ui.sh
```

### Checking after install

```bash
kubectl get pods -n stock-anomaly-detection -l app.kubernetes.io/instance=openhouse-kafka-ui
kubectl get ingress -n stock-anomaly-detection | grep kafka-ui
```

---

## Installing Airflow

Airflow runs the project's daily batch DAGs (`build_rule_context`, `sync_custom_alerts`, the yfinance/NewsAPI/Finnhub loaders) via `SparkKubernetesOperator`, using DAG files synced live from a Git repository.

### Before you start: set up Git-Sync (REQUIRED)

Airflow's `worker`, `triggerer`, and `dag-processor` pods use **git-sync over SSH** to continuously clone DAGs from `git@github.com:thaihung110/airflow-dags.git`. The SSH secret is **not created by Helm** — it must exist _before_ you run `install_airflow.sh`, otherwise those 3 pods get stuck at `Init:0/2` with:

```
MountVolume.SetUp failed for volume "git-sync-ssh-key": secret "airflow-ssh-secret" not found
```

**1. Generate an SSH keypair for git-sync** (no passphrase, so pods can use it non-interactively):

```bash
ssh-keygen -t ed25519 -C "airflow-gitsync" -f ~/.ssh/airflow_gitsync -N ""
```

**2. Create the Kubernetes secret** in the same namespace Airflow will run in:

```bash
kubectl create secret generic airflow-ssh-secret \
  --from-file=gitSshKey=~/.ssh/airflow_gitsync \
  -n stock-anomaly-detection
```

**3. Add the public key as a GitHub deploy key**, so git-sync can pull (read-only) from the DAG repo:

```bash
cat ~/.ssh/airflow_gitsync.pub
```

- Go to the `thaihung110/airflow-dags` repo → **Settings** → **Deploy keys** → **Add deploy key**
- Paste the public key, name it `airflow-gitsync`
- Leave **Write access** unchecked — git-sync only needs to read

Once the secret exists, the 3 pods will automatically restart and finish their init containers — no need to reinstall Airflow.

### 1. Install Airflow

```bash
./scripts/install_airflow.sh
```

This runs:

```bash
helm upgrade --install --namespace stock-anomaly-detection openhouse-airflow apache-airflow/airflow -f config/airflow-no-auth.yaml --timeout 15m0s
```

> Note the `-f config/airflow-no-auth.yaml` — the currently-deployed variant uses Airflow 3's `SimpleAuthManager` with a single fixed `admin` user, not the fuller `config/airflow.yaml`. See "What's customized" below.

**Main configuration:**

- **Version**: `3.0.2` (chart `apache-airflow/airflow` 1.21.0)
- **Executor**: CeleryExecutor (chart default — not customized)
- **PostgreSQL** (subchart): stores Airflow metadata — user `postgres` / password `postgres`, 8Gi persistent storage on `hostpath`
- **Redis** (subchart): Celery broker — 1Gi persistent storage on `hostpath`
- **Ingress**: API server exposed at `openhouse.airflow.test` (Airflow 3.x serves the UI from the API server — there is no separate webserver)

#### What's customized vs. the chart defaults

`config/airflow-no-auth.yaml` is a much larger file than the chart's own default `values.yaml` (it pulls in extra subchart values), so a full line-by-line copy-diff isn't meaningful the way it is for Kafka/Kafka UI. These are the customizations that actually matter, each checked against the chart's default:

| Setting | Chart default | This project | Why it was changed |
|---|---|---|---|
| `extraEnv` (`AIRFLOW__CORE__AUTH_MANAGER`, `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS`, `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS`) | not set (chart's normal FAB-based auth) | `SimpleAuthManager`, fixed user `admin:Admin`, all-admins mode | Simplified single-admin login instead of full RBAC auth — this is why the file is named `airflow-no-auth.yaml`. `config/airflow.yaml` is kept alongside it as the "real auth" alternative but is **not** what `install_airflow.sh` currently deploys (it's referenced only in a commented-out line pointing at a locally-cached chart tarball). |
| `env` (`PYTHONPATH`) | not set | `/opt/airflow/dags/repo/plugins` | Lets DAGs import shared plugin code from the git-synced repo. |
| `ingress.apiServer` | disabled | enabled, host `openhouse.airflow.test`, class `nginx`, `proxy-buffering off` / `proxy-next-upstream off` | Expose the API server outside the cluster; buffering must be off because the API server streams responses. |
| `postgresql.enabled` + `auth` + `primary.persistence` | disabled (expects an external DB) | enabled — user/password `postgres`/`postgres`, `storageClass: hostpath`, `size: 8Gi` | Run Airflow's metadata DB in-cluster instead of requiring an external Postgres. |
| `redis.enabled` + `persistence` | disabled | enabled — `storageClassName: hostpath`, `size: 1Gi` | In-cluster Celery broker for `CeleryExecutor`. |
| `apiServer.startupProbe` | `initialDelaySeconds: 0`, `failureThreshold: 6` | `initialDelaySeconds: 60`, `failureThreshold: 30` | The API server is slow on its first boot (DB migrations); the chart default probe would kill it before it's ready. |

> ⚠️ `scripts/template_airflow.sh` renders `config/airflow.yaml`, while `scripts/install_airflow.sh` deploys `config/airflow-no-auth.yaml` — the preview and the real install currently use **different** files. Keep this in mind if a rendered preview doesn't match what's actually running.

### 2. Preview the rendered manifests (optional)

```bash
./scripts/template_airflow.sh
```

### 3. Uninstall Airflow

```bash
./scripts/uninstall_airflow.sh
```

This does more than a plain `helm uninstall` — it also force-deletes leftover Airflow pods, strips finalizers from PVCs (including the PostgreSQL subchart's) and force-deletes them, and removes leftover ConfigMaps/Secrets labeled with the release name.

### Checking after install

```bash
kubectl get pods -n stock-anomaly-detection -l release=openhouse-airflow
kubectl get ingress -n stock-anomaly-detection | grep airflow
```

---

## RBAC

RBAC resources in `rbac/` are required for Airflow to submit and monitor Spark jobs via the Spark Operator.

### Apply

```bash
kubectl apply -f rbac/airflow-spark-rbac.yaml
kubectl apply -f rbac/spark-submit-clusterrole.yaml
kubectl apply -f rbac/spark-submit-clusterrolebinding.yaml
```

### Resources

**`airflow-spark-rbac.yaml`** — Role + RoleBinding scoped to the `stock-anomaly-detection` namespace.

- Role `airflow-spark-operator`: grants `create/get/list/watch/delete/patch/update` on `sparkapplications` (Spark Operator CRD).
- Bound to: `openhouse-airflow-worker`, `openhouse-airflow-triggerer` (namespace `stock-anomaly-detection`).

**`spark-submit-clusterrole.yaml`** — ClusterRole `spark-submit-role` with cluster-wide permissions:

| Resource                                   | Verbs                                           |
| ------------------------------------------ | ----------------------------------------------- |
| `sparkapplications` (sparkoperator.k8s.io) | create, get, list, watch, update, patch, delete |
| `pods`, `pods/log`                         | get, list, watch                                |
| `services`                                 | get, list                                       |
| `configmaps`                               | get, list                                       |

**`spark-submit-clusterrolebinding.yaml`** — Binds `spark-submit-role` to:

| ServiceAccount                   | Namespace                  | Purpose                                        |
| -------------------------------- | --------------------------- | ---------------------------------------------- |
| `openhouse-spark-operator-spark` | `stock-anomaly-detection` | Spark Operator submitter                       |
| `openhouse-airflow-worker`       | `stock-anomaly-detection` | Airflow Worker (SparkKubernetesOperator)       |
| `openhouse-airflow-triggerer`    | `stock-anomaly-detection` | Airflow Triggerer (async lifecycle monitoring) |

> These ServiceAccounts are created wherever their owning release is installed, and every install script in this project (`install_kafka.sh`, `install_airflow.sh`, `install_spark_operators.sh`) deploys with `--namespace stock-anomaly-detection`. This binding previously pointed at `namespace: default`, which matched no ServiceAccount that actually exists in this cluster — that has been corrected here.

### Why two sets of RBAC?

- `airflow-spark-rbac.yaml` (Role/RoleBinding) grants namespace-scoped access in `stock-anomaly-detection`, where Spark jobs actually run.
- `spark-submit-clusterrole.yaml` + `spark-submit-clusterrolebinding.yaml` (ClusterRole/ClusterRoleBinding) grant the same ServiceAccounts cluster-wide access, so they could also manage `SparkApplication` resources in other namespaces if the platform ever expands beyond `stock-anomaly-detection`.

---

## Scripts Guide

| Script                                                       | Description                                                                                                                         |
| ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| `install_kafka.sh`                                           | Install/upgrade Kafka via Helm                                                                                                      |
| `uninstall_kafka.sh`                                         | Remove the Kafka Helm release                                                                                                       |
| `template_kafka.sh`                                          | Render Kafka manifests into `test_template/` for preview                                                                            |
| `create_kafka_topics_plaintext.sh`                           | Create the project's real V3.3 Kafka topics (PLAINTEXT)                                                                             |
| `install_kafka_ui.sh`                                        | Install/upgrade Kafka UI via Helm                                                                                                   |
| `uninstall_kafka_ui.sh`                                      | Remove the Kafka UI Helm release                                                                                                    |
| `template_kafka_ui.sh`                                       | Render Kafka UI manifests into `test_template/` for preview                                                                         |
| `install_airflow.sh`                                         | Install/upgrade Airflow via Helm (deploys `config/airflow-no-auth.yaml`)                                                            |
| `uninstall_airflow.sh`                                       | Fully remove Airflow: Helm release, pods, PVCs (finalizers stripped), ConfigMaps/Secrets                                            |
| `template_airflow.sh`                                        | Render Airflow manifests into `test_template/` for preview (renders `config/airflow.yaml`)                                          |

---

## Post-install Verification

```bash
# All orchestration pods
kubectl get pods -n stock-anomaly-detection

# Kafka
kubectl get pods -n stock-anomaly-detection -l app.kubernetes.io/instance=openhouse-kafka

# Kafka UI
kubectl get pods -n stock-anomaly-detection -l app.kubernetes.io/instance=openhouse-kafka-ui

# Airflow
kubectl get pods -n stock-anomaly-detection -l release=openhouse-airflow

# RBAC
kubectl get role,rolebinding -n stock-anomaly-detection
kubectl get clusterrole spark-submit-role
kubectl get clusterrolebinding spark-submit-binding
```
