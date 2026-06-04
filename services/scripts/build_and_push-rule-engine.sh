#!/bin/bash
set -e

REGISTRY="hungvt0110"
SERVICE_NAME="rule-engine"
TAG="${1:-latest}"
IMAGE_NAME="$REGISTRY/$SERVICE_NAME:$TAG"
SERVICES_DIR="$(dirname "$0")/.."

echo "Building $IMAGE_NAME..."
docker build -t "$IMAGE_NAME" -f "$SERVICES_DIR/rule-engine/Dockerfile" "$SERVICES_DIR"

echo "Pushing to registry..."
docker push "$IMAGE_NAME"

echo "✓ Successfully pushed $IMAGE_NAME"
