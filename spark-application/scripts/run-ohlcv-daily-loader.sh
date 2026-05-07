#!/bin/bash
set -e

NAMESPACE="stock-anomaly-detection"
APP_NAME="ohlcv-daily-loader"

echo "Deploying $APP_NAME..."
kubectl apply -f "$(dirname "$0")/../k8s/ohlcv-daily-loader-symbols-configmap.yaml"
kubectl apply -f "$(dirname "$0")/../k8s/ohlcv-daily-loader-spark-application.yaml"

echo "Waiting for pod to be ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=$APP_NAME \
  -n $NAMESPACE --timeout=300s 2>/dev/null || true

echo "$APP_NAME started. Monitor with:"
echo "  kubectl logs -f -n $NAMESPACE -l app.kubernetes.io/name=$APP_NAME"
