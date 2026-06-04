#!/bin/bash
set -e

NAMESPACE="stock-anomaly-detection"
DEPLOYMENT_NAME="finnhub-trades-producer"

echo "Deploying $DEPLOYMENT_NAME..."
kubectl apply -f "$(dirname "$0")/../k8s/finnhub-trades-producer/deployment.yaml"

echo "Checking deployment status..."
if kubectl wait --for=condition=available=1 deployment/$DEPLOYMENT_NAME \
  -n $NAMESPACE --timeout=10s 2>/dev/null; then
  echo "$DEPLOYMENT_NAME is ready"
else
  echo "Waiting for $DEPLOYMENT_NAME to become ready (this may take up to 60s)..."
  kubectl wait --for=condition=available=1 deployment/$DEPLOYMENT_NAME \
    -n $NAMESPACE --timeout=60s 2>/dev/null || true
fi

echo "$DEPLOYMENT_NAME started. Monitor with:"
echo "  kubectl logs -f -n $NAMESPACE deployment/$DEPLOYMENT_NAME"
