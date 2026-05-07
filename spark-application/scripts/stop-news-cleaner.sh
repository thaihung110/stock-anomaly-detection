#!/bin/bash
set -e

NAMESPACE="stock-anomaly-detection"
APP_NAME="news-cleaner"

echo "Stopping $APP_NAME..."
kubectl delete sparkapplication $APP_NAME -n $NAMESPACE --ignore-not-found

echo "$APP_NAME stopped."
