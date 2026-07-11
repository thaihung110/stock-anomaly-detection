#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

STORAGE_CLASS="hostpath"

echo "Recreating StorageClass '${STORAGE_CLASS}' with reclaimPolicy: Retain..."
kubectl delete storageclass "${STORAGE_CLASS}" --ignore-not-found
kubectl apply -f config/storageclass.yaml

echo "Patching existing PVs on '${STORAGE_CLASS}' to reclaimPolicy: Retain..."
for pv in $(kubectl get pv -o jsonpath="{range .items[?(@.spec.storageClassName=='${STORAGE_CLASS}')]}{.metadata.name}{'\n'}{end}"); do
  echo "  - ${pv}"
  kubectl patch pv "${pv}" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
done

echo "Done. New PVCs on '${STORAGE_CLASS}' will provision Retain PVs; existing PVs are now protected from accidental PVC deletion too."
