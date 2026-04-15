#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm upgrade --install --namespace stock-anomaly-detection openhouse-keycloak bitnami/keycloak -f config/keycloak.yaml
