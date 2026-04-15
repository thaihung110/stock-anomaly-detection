#!/bin/bash

# Script to build and push Docker image for Spark History Server to Docker Hub
# Cùng pattern với build-image.sh của các Spark jobs

set -eu

# Configuration
DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-hungvt0110}"
IMAGE_NAME="${IMAGE_NAME:-spark-history-server}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
PUSH_TO_DOCKERHUB="${PUSH_TO_DOCKERHUB:-true}"

# Full image name for Docker Hub
FULL_IMAGE_NAME="${DOCKERHUB_USERNAME}/${IMAGE_NAME}:${IMAGE_TAG}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "${SCRIPT_DIR}"

echo "=========================================="
echo "Building Docker image for Spark History Server"
echo "=========================================="
echo "Docker Hub Username: ${DOCKERHUB_USERNAME}"
echo "Image Name:          ${IMAGE_NAME}"
echo "Tag:                 ${IMAGE_TAG}"
echo "Full Image:          ${FULL_IMAGE_NAME}"
echo "=========================================="

# Build image
echo ""
echo "Step 1: Building Docker image..."
docker build -t "${FULL_IMAGE_NAME}" .

# Also tag as latest if different tag is used
if [ "${IMAGE_TAG}" != "latest" ]; then
    LATEST_TAG="${DOCKERHUB_USERNAME}/${IMAGE_NAME}:latest"
    echo "Tagging as latest: ${LATEST_TAG}"
    docker tag "${FULL_IMAGE_NAME}" "${LATEST_TAG}"
fi

# Push to Docker Hub
if [ "${PUSH_TO_DOCKERHUB}" = "true" ]; then
    echo ""
    echo "Step 2: Pushing to Docker Hub..."
    echo "Make sure you are logged in to Docker Hub:"
    echo "  docker login -u ${DOCKERHUB_USERNAME}"
    echo ""

    docker push "${FULL_IMAGE_NAME}"

    if [ "${IMAGE_TAG}" != "latest" ]; then
        docker push "${LATEST_TAG}"
    fi

    echo ""
    echo "=========================================="
    echo "✓ Image pushed successfully!"
    echo "Image: ${FULL_IMAGE_NAME}"
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "✓ Image built locally (not pushed)"
    echo "Image: ${FULL_IMAGE_NAME}"
    echo "To push, set PUSH_TO_DOCKERHUB=true"
    echo "=========================================="
fi
