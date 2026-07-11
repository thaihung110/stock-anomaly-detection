#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

NAMESPACE="stock-anomaly-detection"
RELEASE="openhouse-postgresql"

helm uninstall --namespace "${NAMESPACE}" "${RELEASE}"
