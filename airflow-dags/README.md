# airflow-dags

Airflow DAGs and a shared plugin package that orchestrate the daily/weekly Spark batch pipeline — submitting `SparkApplication` custom resources and waiting for them to finish.

> ⚠️ **This directory is a mirror, not the live source.** Airflow's `worker`/`triggerer`/`dag-processor` pods git-sync from a **separate GitHub repo**, `git@github.com:thaihung110/airflow-dags.git` (branch `main`) — not from this monorepo. Editing files here has **no effect** on the running Airflow instance until they are pushed to that repo. See [Deployment Model](#deployment-model-this-directory-is-a-mirror) below.

## Structure

```
airflow-dags/
├── dags/
│   ├── spark_batch_pipeline_dag.py       # 3 production DAGs (scheduled)
│   ├── spark_batch_pipeline_test_dag.py  # same 3 pipelines, manually-triggered, no wait buffers
│   └── template-spark-dag.py             # generic Jinja2-templated Spark-submit demo — NOT part of this project's live pipeline (see Known Issues)
├── plugins/spark_kubernetes/
│   ├── operators.py   # spark_application_task() — the TaskGroup builder every DAG uses
│   ├── sensors.py      # SparkLifecycleSensor + SparkHistoryLink
│   └── triggers.py     # SparkLifecycleTrigger — async poll loop, runs on the Triggerer
└── pyrightconfig.json  # extraPaths: ["plugins"], so the IDE resolves `from spark_kubernetes import ...`
```

## Deployment Model: This Directory Is a Mirror

Per `infra/k8s/orchestration/config/airflow-no-auth.yaml` (the file `install_airflow.sh` actually deploys):

```yaml
env:
  - name: PYTHONPATH
    value: "/opt/airflow/dags/repo/plugins"   # global env, so worker + triggerer + scheduler all get it

dags:
  gitSync:
    enabled: true
    repo: git@github.com:thaihung110/airflow-dags.git
    branch: main
    subPath: "dags"   # Airflow's DAG bag looks inside repo/dags for *.py DAG files
```

git-sync clones the **entire** `thaihung110/airflow-dags` repo into `/opt/airflow/dags/repo` on every pod. Airflow then loads DAGs from `repo/dags/`, and `PYTHONPATH=/opt/airflow/dags/repo/plugins` makes `repo/plugins/spark_kubernetes` importable from any DAG file — which is exactly why that external repo needs the same `dags/` + `plugins/` layout as this local directory.

There is no automated sync configured in this monorepo (no submodule, no subtree remote, no CI job) — pushing a change means mirroring this directory's contents to the external repo yourself, e.g.:

```bash
# One-time: add the external repo as a second remote
git remote add airflow-dags git@github.com:thaihung110/airflow-dags.git

# Each time you want to publish a change from this directory:
git subtree push --prefix=airflow-dags airflow-dags main
```

(`git subtree push` is a reasonable default here since `airflow-dags/` is a real subdirectory of this monorepo with its own coherent history — adjust to whatever workflow you're actually using if this isn't it.)

Setting up the SSH deploy key git-sync needs to read that repo is covered in
[`infra/k8s/orchestration/README.md`](../infra/k8s/orchestration/README.md) → **"Before you start: set up Git-Sync (REQUIRED)"** — do that before `install_airflow.sh`, or the `worker`/`triggerer`/`dag-processor` pods sit stuck at `Init:0/2` waiting on a secret that Helm doesn't create for you.

## Shared Mechanism: `spark_application_task()`

Every DAG in this directory builds its tasks through one function, `spark_application_task(manifest_filename)` in `plugins/spark_kubernetes/operators.py`. It returns a 3-task `TaskGroup` (not a bare task) so that `>>` chaining between apps gates the *entire* load → submit → monitor sequence, not just the final step:

1. **`load_manifest`** (`load_spark_manifest`, a `@task`): reads `spark-application/k8s/<manifest_filename>` — the **same YAML files** documented in `spark-application/README.md` — resolved via `Path(__file__).resolve().parents[2] / "spark-application" / "k8s"` (i.e. this only works when `airflow-dags/` and `spark-application/` are siblings under the same repo root, which is true in this monorepo but must also hold in the external `thaihung110/airflow-dags` repo — worth verifying `spark-application/` is present there too when setting that repo up).
   Deep-copies the manifest, renames it `f"{base_name}-{run_suffix}"` (`run_suffix` = `{{ ts_nodash | lower }}`, truncated to 63 chars for the k8s name limit), and adds labels `spark-app-template-name` (original name) + `airflow-managed: "true"`.
2. **`submit`** (`DictSparkKubernetesOperator`): creates the `SparkApplication` custom resource via `KubernetesHook.create_custom_object`, targeting whatever `metadata.namespace` the manifest itself declares (all current manifests use `stock-anomaly-detection`). Pushes `job_name`/`namespace` to XCom.
3. **`monitor`** (`SparkLifecycleSensor`): **deferred** — hands off to `SparkLifecycleTrigger` (`plugins/spark_kubernetes/triggers.py`), which polls the CR's `status.applicationState.state` every 10s via `kubernetes_asyncio` on the **Triggerer** process, not a worker slot. Resolves on `COMPLETED`/`SUCCEEDED` (with up to 6 retries if the app ID isn't populated yet) or raises `AirflowException` on `FAILED`/`SUBMISSION_FAILED` — with a synchronous re-verification against the K8s API (`_verify_status_sync`) before giving up, in case the trigger's own poll caught a transient error rather than a real failure. Exposes a **"Spark History"** link in the Airflow UI (`SparkHistoryLink`) pointing at `https://openhouse.spark-history.test/history/<spark_app_id>` — this host doesn't appear to be wired to a real Ingress/DNS entry anywhere else in the repo (see Known Issues).

Deliberately **no `on_failure_callback`** to delete the CR on failure (unlike `template-spark-dag.py`'s version, which does) — a failed `SparkApplication` is left in place so its driver/executor pod logs stay inspectable; the Spark Operator garbage-collects it automatically once the manifest's `timeToLiveSeconds` elapses.

## DAGs

### Production — `spark_batch_pipeline_dag.py`

| DAG | Schedule | Chain |
|---|---|---|
| `spark_ohlcv_daily_pipeline` | `0 6 * * *` (06:00 UTC daily) | `ohlcv-daily-loader` → *wait till 06:30* → `ohlcv-daily-cleaner` → *wait till 07:00* → `fact-ohlcv-daily-builder` → *wait till 07:15* → `rule-engine-context-builder` → *wait till 07:30* → `sync-custom-alerts` |
| `spark_news_daily_pipeline` | `0 6 * * *` | `news-cleaner` (single task, no dependency) |
| `spark_batch_weekly_dimension_pipeline` | `0 5 * * 0` (Sunday 05:00 UTC) | `company-info-loader` → `dim-loader` |

The `TimeSensor` waits between steps in `spark_ohlcv_daily_pipeline` are fixed wall-clock buffers, not "wait for the previous task" — each step is scheduled to have finished well before its downstream `wait_until_HHMM` fires, but the sensor itself doesn't inspect the upstream task's actual runtime.

### Test — `spark_batch_pipeline_test_dag.py`

Same 3 pipelines, `schedule=None` (manually triggered from the UI/CLI), `TimeSensor` buffers removed, tasks chained directly back-to-back. Two prerequisites called out in the file's own comments:

- `rule_engine_context_builder` requires `spark_batch_weekly_dimension_pipeline_test` to have populated `dim_symbol` at least once first — otherwise it fails with a clear "run dim-loader first" error (per `spark-application/rule-engine-context-builder/README.md`'s guard check).
- `sync_custom_alerts` is safe to run even against an empty `user_alert_events` table — the watermark starts at epoch, so a 0-row sync is expected and not an error, not a signal something's broken.

### Reference only — `template-spark-dag.py`

A generic, parameterized `SparkKubernetesOperator` DAG with a Jinja2-rendered manifest and Airflow UI `Param`s for image/resources/args. **Not wired into this project**: it targets namespace `dmp-lakehouse-demo`, Spark History host `spark-history.dmp.demo`, and secret `minio-root-credentials` — none of which match this project's real namespace (`stock-anomaly-detection`) or secrets (`spark-app-secrets`, per `spark-application/k8s/spark-app-secrets.yaml`). Treat it as a worked example for building a fully dynamic Spark-submit DAG, not as something to schedule.

## Spark App Coverage

8 of the 10 apps in `spark-application/` are orchestrated by these DAGs. The 2 missing — `news-ingest-stream` and `trades-ohlcv-stream` — are **Spark Structured Streaming** jobs, not batch: they're meant to run continuously, so they're deployed once directly via their own `spark-application/scripts/run-<app>.sh` (see `spark-application/README.md`) rather than scheduled here. A `SparkApplication` with `restartPolicy: Always` (streaming) doesn't fit Airflow's run-to-completion task model the way a batch job does.

## Prerequisites

- An Airflow connection named `kubernetes_default` (the in-cluster default, normally available with no extra setup when Airflow itself runs inside the cluster it submits jobs to).
- The `spark-app-secrets` Kubernetes Secret and any per-app ConfigMaps referenced by the manifests under `spark-application/k8s/` must already exist in `stock-anomaly-detection` — these DAGs submit the manifests as-is; they don't create supporting Secrets/ConfigMaps.
- Whatever image tag each `spark-application/k8s/<app>-spark-application.yaml` currently references must already be pushed to a reachable registry — see `spark-application/README.md`'s Build and Push section.

## Known Issues

- **No automated mechanism keeps this directory in sync with the deployed `thaihung110/airflow-dags` repo** — a change committed here is invisible to Airflow until manually pushed there (see [Deployment Model](#deployment-model-this-directory-is-a-mirror)).
- **`SparkHistoryLink`'s host (`https://openhouse.spark-history.test`) doesn't correspond to any Ingress or DNS entry found elsewhere in this repo** — clicking "Spark History" in the Airflow UI likely resolves to nothing today. Either a real Spark History Server needs to be deployed and exposed at that host, or the constant needs updating.
- `template-spark-dag.py` uses demo/placeholder namespace and secret names — if left in the DAGs folder as-is, it would fail immediately on trigger (wrong namespace, missing secret) rather than doing anything destructive, but it adds noise to the DAG list.
- The `TimeSensor` buffers in the production OHLCV pipeline assume each upstream Spark job reliably finishes within its allotted window (30 min for the loader, 30 min for the cleaner, 15 min for the fact builder, 15 min for the context builder) — a slow run doesn't get detected as such by the pipeline itself; the next step just starts on schedule regardless of whether the previous one is actually done.

## Testing

No automated tests for either the DAGs or the `spark_kubernetes` plugin — no `tests/` directory exists in this folder.
