#!/bin/bash
set -e

REGISTRY="hungvt0110"
SERVICE_NAME="finnhub-news-producer"
TAG="${1:-latest}"
IMAGE_NAME="$REGISTRY/$SERVICE_NAME:$TAG"
SERVICE_DIR="$(dirname "$0")/../finnhub-news-producer"

echo "Building $IMAGE_NAME..."
docker build -t "$IMAGE_NAME" "$SERVICE_DIR"

echo "Pushing to registry..."
docker push "$IMAGE_NAME"

echo "✓ Successfully pushed $IMAGE_NAME"
