#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm upgrade --install --namespace stock-anomaly-detection openhouse-airflow apache-airflow/airflow -f config/airflow-no-auth.yaml --timeout 15m0s


# helm upgrade --install --namespace stock-anomaly-detection openhouse-airflow /tmp/airflow-1.21.0.tgz -f config/airflow.yaml --timeout 15m0s 

