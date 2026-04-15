#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

helm template --namespace stock-anomaly-detection openhouse-gravitino oci://registry-1.docker.io/bitnamicharts/gravitino -f config/gravitino.yaml > test_template/gravitino_template.yaml
