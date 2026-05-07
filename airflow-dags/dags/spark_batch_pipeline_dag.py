import asyncio
import copy
import logging
from datetime import time
from pathlib import Path
from typing import Any, Dict, Tuple

import pendulum
import yaml

from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowException
from airflow.models import BaseOperatorLink, XCom
from airflow.models.taskinstance import TaskInstanceKey
from airflow.providers.cncf.kubernetes.hooks.kubernetes import KubernetesHook
from airflow.providers.cncf.kubernetes.operators.spark_kubernetes import (
    SparkKubernetesOperator,
)
from airflow.sensors.base import BaseSensorOperator
from airflow.sensors.time_sensor import TimeSensor
from airflow.triggers.base import BaseTrigger, TriggerEvent
from airflow.utils.context import Context
from kubernetes_asyncio import client as async_client
from kubernetes_asyncio import config as async_config

logger = logging.getLogger("airflow.task")

MARKET_TZ = pendulum.timezone("UTC")
REPO_ROOT = Path(__file__).resolve().parents[2]
SPARK_MANIFEST_DIR = REPO_ROOT / "spark-application" / "k8s"
SPARK_HISTORY_HOST = "https://openhouse.spark-history.test"


class SparkHistoryLink(BaseOperatorLink):
    name = "Spark History"

    def get_link(self, operator, *, ti_key: TaskInstanceKey) -> str:
        try:
            spark_app_id = XCom.get_value(key="spark_app_id", ti_key=ti_key)
            if spark_app_id:
                return f"{SPARK_HISTORY_HOST}/history/{spark_app_id}"
        except Exception:
            pass
        return SPARK_HISTORY_HOST


class SparkLifecycleTrigger(BaseTrigger):
    def __init__(self, name: str, namespace: str, poll_interval: int = 10):
        super().__init__()
        self.name = name
        self.namespace = namespace
        self.poll_interval = poll_interval

    def serialize(self) -> Tuple[str, Dict[str, Any]]:
        return (
            f"{self.__class__.__module__}.{self.__class__.__qualname__}",
            {
                "name": self.name,
                "namespace": self.namespace,
                "poll_interval": self.poll_interval,
            },
        )

    async def run(self):
        try:
            await async_config.load_incluster_config()
        except Exception:
            await async_config.load_kube_config()

        async with async_client.ApiClient() as api_client:
            api = async_client.CustomObjectsApi(api_client)
            missing_id_retries = 0
            max_id_retries = 6

            while True:
                try:
                    resource = await api.get_namespaced_custom_object(
                        group="sparkoperator.k8s.io",
                        version="v1beta2",
                        namespace=self.namespace,
                        plural="sparkapplications",
                        name=self.name,
                    )
                    status = resource.get("status", {})
                    app_state = status.get("applicationState", {})
                    state = app_state.get("state", "UNKNOWN")
                    app_id = status.get("sparkApplicationId") or app_state.get(
                        "sparkApplicationId"
                    )

                    if state in ["COMPLETED", "SUCCEEDED"]:
                        if not app_id and missing_id_retries < max_id_retries:
                            missing_id_retries += 1
                            await asyncio.sleep(self.poll_interval)
                            continue
                        yield TriggerEvent(
                            {"status": "success", "spark_app_id": app_id}
                        )
                        return

                    if state in ["FAILED", "SUBMISSION_FAILED"]:
                        err = app_state.get("errorMessage", "Unknown K8s Error")
                        yield TriggerEvent(
                            {
                                "status": "failed",
                                "spark_app_id": app_id,
                                "message": f"{state}: {err}",
                            }
                        )
                        return

                    await asyncio.sleep(self.poll_interval)

                except async_client.ApiException as e:
                    if e.status == 404:
                        await asyncio.sleep(self.poll_interval)
                        continue
                    yield TriggerEvent({"status": "error", "message": str(e)})
                    return
                except Exception as e:
                    yield TriggerEvent({"status": "error", "message": str(e)})
                    return


class SparkLifecycleSensor(BaseSensorOperator):
    operator_extra_links = (SparkHistoryLink(),)
    template_fields = ("name", "namespace")

    def __init__(self, name: str, namespace: str, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.namespace = namespace

    def execute(self, context: Context):
        self.defer(
            trigger=SparkLifecycleTrigger(
                name=self.name,
                namespace=self.namespace,
            ),
            method_name="execute_complete",
        )

    def execute_complete(self, context: Context, event: Dict[str, Any]):
        status = event.get("status")
        app_id = event.get("spark_app_id")
        msg = event.get("message", "")

        if app_id:
            context["ti"].xcom_push(key="spark_app_id", value=app_id)

        if status == "success":
            logger.info("Spark job succeeded. App ID: %s", app_id)
            return

        real_state, real_id = self._verify_status_sync()
        if real_state in ["COMPLETED", "SUCCEEDED"]:
            if real_id:
                context["ti"].xcom_push(key="spark_app_id", value=real_id)
            return

        raise AirflowException(
            f"Spark job failed. Final state: {real_state}. Details: {msg}"
        )

    def _verify_status_sync(self):
        try:
            hook = KubernetesHook(conn_id="kubernetes_default")
            crd = hook.get_custom_object(
                group="sparkoperator.k8s.io",
                version="v1beta2",
                namespace=self.namespace,
                plural="sparkapplications",
                name=self.name,
            )
            status = crd.get("status", {})
            state = status.get("applicationState", {}).get("state", "UNKNOWN")
            app_id = status.get("sparkApplicationId") or status.get(
                "applicationState", {}
            ).get("sparkApplicationId")
            return state, app_id
        except Exception as e:
            logger.error("Sync verification failed: %s", e)
            return "UNKNOWN", None


class DictSparkKubernetesOperator(SparkKubernetesOperator):
    def execute(self, context):
        if not isinstance(self.application_file, dict):
            return super().execute(context)

        body = self.application_file
        meta = body.get("metadata", {})
        name = meta.get("name")
        namespace = self.namespace or meta.get("namespace", "default")

        hook = KubernetesHook(conn_id=self.kubernetes_conn_id)
        logger.info(
            "Submitting SparkApplication %s in namespace %s", name, namespace
        )
        hook.create_custom_object(
            "sparkoperator.k8s.io",
            "v1beta2",
            "sparkapplications",
            body,
            namespace,
        )

        context["ti"].xcom_push(key="job_name", value=name)
        context["ti"].xcom_push(key="namespace", value=namespace)
        return {"job_name": name, "namespace": namespace}


@task
def load_spark_manifest(
    manifest_filename: str, run_suffix: str
) -> Dict[str, Any]:
    manifest_path = SPARK_MANIFEST_DIR / manifest_filename
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        manifest = yaml.safe_load(manifest_file)

    body = copy.deepcopy(manifest)
    metadata = body.setdefault("metadata", {})
    base_name = metadata["name"]
    metadata["name"] = f"{base_name}-{run_suffix}"[:63].rstrip("-")

    labels = metadata.setdefault("labels", {})
    labels["spark-app-template-name"] = base_name
    labels["airflow-managed"] = "true"

    return body


def delete_spark_job_on_failure(context):
    task_id = context["task"].task_id
    submit_task_id = task_id.replace("monitor_", "submit_", 1)
    job_details = context["ti"].xcom_pull(
        task_ids=submit_task_id, key="return_value"
    )
    if not job_details:
        return

    name = job_details.get("job_name")
    namespace = job_details.get("namespace")
    try:
        hook = KubernetesHook(conn_id="kubernetes_default")
        hook.delete_custom_object(
            group="sparkoperator.k8s.io",
            version="v1beta2",
            namespace=namespace,
            plural="sparkapplications",
            name=name,
        )
        logger.info("Deleted SparkApplication after failure: %s", name)
    except Exception as e:
        logger.error("Failed to delete SparkApplication %s: %s", name, e)


def spark_application_task(manifest_filename: str):
    task_name = manifest_filename.removesuffix("-spark-application.yaml")
    task_name = task_name.replace("-", "_")

    manifest = load_spark_manifest.override(
        task_id=f"load_{task_name}_manifest"
    )(
        manifest_filename=manifest_filename,
        run_suffix="{{ ts_nodash | lower }}",
    )

    submit = DictSparkKubernetesOperator(
        task_id=f"submit_{task_name}",
        kubernetes_conn_id="kubernetes_default",
        namespace="{{ ti.xcom_pull(task_ids='load_"
        + task_name
        + "_manifest')['metadata']['namespace'] }}",
        application_file=manifest,
        do_xcom_push=True,
    )

    monitor = SparkLifecycleSensor(
        task_id=f"monitor_{task_name}",
        name=submit.output["job_name"],
        namespace=submit.output["namespace"],
        on_failure_callback=delete_spark_job_on_failure,
    )

    manifest >> submit >> monitor
    return monitor


default_args = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 0,
}


with DAG(
    dag_id="spark_ohlcv_daily_pipeline",
    default_args=default_args,
    start_date=pendulum.datetime(2026, 1, 1, tz=MARKET_TZ),
    schedule="0 6 * * *",
    catchup=False,
    tags=["spark", "batch", "daily", "ohlcv"],
) as ohlcv_daily_dag:
    wait_0630 = TimeSensor(task_id="wait_until_0630", target_time=time(6, 30))
    wait_0700 = TimeSensor(task_id="wait_until_0700", target_time=time(7, 0))
    wait_0715 = TimeSensor(task_id="wait_until_0715", target_time=time(7, 15))

    ohlcv_daily_loader = spark_application_task(
        "ohlcv-daily-loader-spark-application.yaml"
    )
    ohlcv_daily_cleaner = spark_application_task(
        "ohlcv-daily-cleaner-spark-application.yaml"
    )
    fact_ohlcv_daily_builder = spark_application_task(
        "fact-ohlcv-daily-builder-spark-application.yaml"
    )
    rule_engine_context_builder = spark_application_task(
        "rule-engine-context-builder-spark-application.yaml"
    )

    ohlcv_daily_loader >> wait_0630 >> ohlcv_daily_cleaner
    ohlcv_daily_cleaner >> wait_0700 >> fact_ohlcv_daily_builder
    fact_ohlcv_daily_builder >> wait_0715 >> rule_engine_context_builder


with DAG(
    dag_id="spark_news_daily_pipeline",
    default_args=default_args,
    start_date=pendulum.datetime(2026, 1, 1, tz=MARKET_TZ),
    schedule="0 6 * * *",
    catchup=False,
    tags=["spark", "batch", "daily", "news"],
) as news_daily_dag:
    spark_application_task("news-cleaner-spark-application.yaml")


with DAG(
    dag_id="spark_batch_weekly_dimension_pipeline",
    default_args=default_args,
    start_date=pendulum.datetime(2026, 1, 4, tz=MARKET_TZ),
    schedule="0 5 * * 0",
    catchup=False,
    tags=["spark", "batch", "weekly", "dimension"],
) as weekly_dag:
    company_info_loader = spark_application_task(
        "company-info-loader-spark-application.yaml"
    )
    dim_loader = spark_application_task("dim-loader-spark-application.yaml")

    company_info_loader >> dim_loader
