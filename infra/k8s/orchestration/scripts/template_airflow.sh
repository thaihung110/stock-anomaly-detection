#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm template --namespace stock-anomaly-detection openhouse-airflow apache-airflow/airflow -f config/airflow.yaml > test_template/airflow_template.yaml

