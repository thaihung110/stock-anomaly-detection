#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm template --namespace stock-anomaly-detection openhouse-nifi dysnix/nifi -f config/nifi.yaml > test_template/nifi_template.yaml

