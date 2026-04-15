#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

echo "Uninstalling FinnhubProducer..."
kubectl delete -f application/finnhub-producer.yaml -n stock-anomaly-detection --ignore-not-found=true

echo "Waiting for resources to be deleted..."
sleep 5

echo "Checking if resources are deleted..."
kubectl get deployment finnhub-producer -n stock-anomaly-detection 2>/dev/null && echo "Warning: Deployment still exists" || echo "Deployment deleted successfully"
kubectl get secret finnhub-api-secret -n stock-anomaly-detection 2>/dev/null && echo "Warning: Secret still exists" || echo "Secret deleted successfully"
kubectl get configmap finnhub-producer-config -n stock-anomaly-detection 2>/dev/null && echo "Warning: ConfigMap still exists" || echo "ConfigMap deleted successfully"

echo ""
echo "FinnhubProducer uninstalled successfully!"

