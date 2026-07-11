#!/bin/bash

set -eu

NAMESPACE="stock-anomaly-detection"

helm uninstall --namespace "${NAMESPACE}" openhouse-trino
