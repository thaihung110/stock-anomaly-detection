#!/bin/bash
set -euo pipefail

NAMESPACE="stock-anomaly-detection"
DEPLOYMENT_NAME="telegram-bot"
TIMEOUT="${DEPLOY_TIMEOUT:-120s}"

echo "Deploying $DEPLOYMENT_NAME..."
kubectl apply -f "$(dirname "$0")/../k8s/telegram-bot/deployment.yaml"

echo "Waiting for $DEPLOYMENT_NAME rollout (timeout: $TIMEOUT)..."
if kubectl rollout status deployment/$DEPLOYMENT_NAME -n $NAMESPACE --timeout=$TIMEOUT; then
  echo "✓ $DEPLOYMENT_NAME is ready"
else
  echo "❌ $DEPLOYMENT_NAME rollout failed. Diagnosing..."
  echo ""
  echo "Pod status:"
  kubectl get pods -n $NAMESPACE -l app=$DEPLOYMENT_NAME -o wide
  echo ""
  echo "Recent events:"
  kubectl describe deployment/$DEPLOYMENT_NAME -n $NAMESPACE | grep -A 30 "^Events:"
  exit 1
fi

echo ""
echo "Monitor with:"
echo "  kubectl logs -f -n $NAMESPACE deployment/$DEPLOYMENT_NAME"
