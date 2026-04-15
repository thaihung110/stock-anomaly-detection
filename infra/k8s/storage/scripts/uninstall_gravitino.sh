#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm uninstall --namespace stock-anomaly-detection openhouse-gravitino

echo "Deleting PVCs for gravitino..."
kubectl delete pvc -l app.kubernetes.io/instance=openhouse-gravitino --ignore-not-found=true
kubectl delete pvc -l release=openhouse-gravitino --ignore-not-found=true
echo "Done."
