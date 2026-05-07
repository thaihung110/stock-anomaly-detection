#!/bin/bash

set -eu

NS="stock-anomaly-detection"
RELEASE="openhouse-airflow"

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

echo "Uninstalling Helm release ${RELEASE}..."
helm uninstall --namespace "${NS}" "${RELEASE}" --ignore-not-found || true

echo "Force deleting all Airflow pods..."
kubectl delete pods -n "${NS}" -l release="${RELEASE}" --force --grace-period=0 --ignore-not-found || true

echo "Removing finalizers and force deleting all Airflow PVCs..."
for pvc in $(kubectl get pvc -n "${NS}" -l release="${RELEASE}" -o name 2>/dev/null); do
  kubectl patch "${pvc}" -n "${NS}" -p '{"metadata":{"finalizers":null}}' || true
  kubectl delete "${pvc}" -n "${NS}" --force --grace-period=0 --ignore-not-found || true
done

# Also catch PVCs not labeled (e.g. postgresql sub-chart PVCs)
for pvc in $(kubectl get pvc -n "${NS}" -o name 2>/dev/null | grep "${RELEASE}"); do
  kubectl patch "${pvc}" -n "${NS}" -p '{"metadata":{"finalizers":null}}' || true
  kubectl delete "${pvc}" -n "${NS}" --force --grace-period=0 --ignore-not-found || true
done

echo "Removing leftover ConfigMaps and Secrets from release..."
kubectl delete configmap,secret -n "${NS}" -l release="${RELEASE}" --ignore-not-found || true

echo "Done. All Airflow resources removed."
