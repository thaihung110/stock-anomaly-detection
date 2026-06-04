#!/bin/bash

set -eu

NAMESPACE="stock-anomaly-detection"

helm uninstall --namespace "${NAMESPACE}" openhouse-trino

# Delete PVC for dynamic catalog store
kubectl delete pvc catalogs-pvc --namespace "${NAMESPACE}" --ignore-not-found
