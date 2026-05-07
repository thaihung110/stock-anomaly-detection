#!/bin/bash
set -e

NAMESPACE="stock-anomaly-detection"
APP_NAME="trades-ohlcv-stream"

echo "Stopping $APP_NAME..."
kubectl delete sparkApplication $APP_NAME -n $NAMESPACE --ignore-not-found=true

echo "$APP_NAME stopped."
