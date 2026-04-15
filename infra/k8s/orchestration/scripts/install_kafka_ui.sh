#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm upgrade --install --namespace stock-anomaly-detection openhouse-kafka-ui kafka-ui/kafka-ui -f config/kafka-ui.yaml

