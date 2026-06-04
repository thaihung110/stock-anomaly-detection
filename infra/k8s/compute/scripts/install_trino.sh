#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

NAMESPACE="stock-anomaly-detection"

# Create PVC for dynamic catalog store (coordinator mounts at /etc/trino/dynamic-catalog)
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: catalogs-pvc
  namespace: ${NAMESPACE}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: hostpath
  resources:
    requests:
      storage: 1Gi
EOF

helm upgrade --install --namespace "${NAMESPACE}" openhouse-trino trino/trino -f config/trino.yaml
