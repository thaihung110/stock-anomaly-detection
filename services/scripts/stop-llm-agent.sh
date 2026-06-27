#!/bin/bash
set -e

NAMESPACE="stock-anomaly-detection"
DEPLOYMENT_NAME="llm-agent"

echo "Stopping $DEPLOYMENT_NAME..."
kubectl delete deployment $DEPLOYMENT_NAME -n $NAMESPACE --ignore-not-found=true

echo "$DEPLOYMENT_NAME stopped."
