#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm uninstall --namespace stock-anomaly-detection openhouse-airflow

# Helm uninstall does NOT delete PVCs automatically (by design, to preserve data).
# However leftover PVCs from the previous release will block the next install
# (pods stuck in Pending because the old PV is gone but PVC still exists).
# So we clean them up explicitly here.
echo "Cleaning up leftover PVCs from release openhouse-airflow..."
kubectl delete pvc -l release=openhouse-airflow --ignore-not-found=true
echo "Done."
