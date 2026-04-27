#!/bin/bash
set -e

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 v1.0"
  exit 1
fi

VERSION="$1"
REGISTRY="hungvt0110"
SERVICE_NAME="ohlcv-daily-loader"
IMAGE_NAME="$REGISTRY/$SERVICE_NAME:$VERSION"
APP_DIR="$(dirname "$0")/../ohlcv-daily-loader"

echo "[1/2] Building Docker image: $IMAGE_NAME..."
docker build -f "$APP_DIR/Dockerfile" -t "$IMAGE_NAME" "$APP_DIR"

echo "[2/2] Pushing to registry..."
docker push "$IMAGE_NAME"

echo "✓ Successfully pushed $IMAGE_NAME"
