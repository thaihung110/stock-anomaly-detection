#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

NAMESPACE="stock-anomaly-detection"
RELEASE="openhouse-minio"

helm uninstall --namespace "${NAMESPACE}" "${RELEASE}"

echo "Deleting PVCs for release ${RELEASE}..."
kubectl delete pvc \
  --namespace "${NAMESPACE}" \
  --selector "app.kubernetes.io/instance=${RELEASE}" \
  --ignore-not-found
