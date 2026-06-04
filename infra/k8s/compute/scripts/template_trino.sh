#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm template --namespace stock-anomaly-detection openhouse-trino trino/trino -f config/trino.yaml > test_template/trino_template.yaml
