#!/bin/bash
set -e

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <version>"
  echo "Example: $0 v0.1"
  exit 1
fi

VERSION="$1"
REGISTRY="hungvt0110"
SERVICE_NAME="trades-ohlcv-stream"
IMAGE_NAME="$REGISTRY/$SERVICE_NAME:$VERSION"
APP_DIR="$(dirname "$0")/../trades-ohlcv-stream"

echo "[1/2] Building Docker image: $IMAGE_NAME..."
docker build -f "$APP_DIR/Dockerfile" -t "$IMAGE_NAME" "$APP_DIR"

echo "[2/2] Pushing to registry..."
docker push "$IMAGE_NAME"

echo "✓ Successfully pushed $IMAGE_NAME"
