#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Script: create_secret_gravitino_tls.sh
#
# Mục đích:
#   Tạo Kubernetes Secret chứa Keycloak CA certificate để Gravitino JVM
#   có thể trust TLS khi fetch OIDC discovery từ HTTPS endpoint.
#
# Cách dùng:
#   ./scripts/create_secret_gravitino_tls.sh
#
# Chỉ cần chạy 1 lần (hoặc khi cert thay đổi).
# ─────────────────────────────────────────────────────────────────────────────

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}/" )/.." && pwd )"
CERT_FILE="${APP_DIR}/tls/keycloak_tls.cert"
SECRET_NAME="keycloak-ca-cert"
NAMESPACE="stock-anomaly-detection"

if [ ! -f "${CERT_FILE}" ]; then
  echo "❌ Không tìm thấy cert file: ${CERT_FILE}"
  exit 1
fi

echo "🔐 Tạo Secret '${SECRET_NAME}' từ ${CERT_FILE}..."

kubectl create secret generic "${SECRET_NAME}" \
  --from-file=ca.crt="${CERT_FILE}" \
  --namespace="${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "✅ Secret '${SECRET_NAME}' đã được tạo/cập nhật."
echo ""
echo "Verify:"
kubectl get secret "${SECRET_NAME}" -n "${NAMESPACE}"
