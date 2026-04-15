#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Script: create_secret_gravitino_tls_ingress.sh
#
# Mục đích:
#   Tạo self-signed TLS certificate cho openhouse.gravitino.test và
#   tạo Kubernetes Secret "gravitino-tls" để sử dụng trong ingress.
#
#   Khác với create_secret_gravitino_tls.sh (tạo keycloak-ca-cert cho JVM truststore),
#   script này tạo TLS cert cho ingress HTTPS của Gravitino.
#
# Cách dùng:
#   ./scripts/create_secret_gravitino_tls_ingress.sh
#
# Chỉ cần chạy 1 lần (hoặc khi cert thay đổi).
# ─────────────────────────────────────────────────────────────────────────────

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

DOMAIN="openhouse.gravitino.test"
KEY_FILE="tls/gravitino_ingress_tls.key"
CERT_FILE="tls/gravitino_ingress_tls.cert"
SECRET_NAME="gravitino-tls"
NAMESPACE="stock-anomaly-detection"

mkdir -p "$(dirname "$KEY_FILE")"

echo "🔐 Generating self-signed TLS certificate for ${DOMAIN} ..."

openssl req -x509 -nodes -days 10000 \
  -newkey rsa:2048 \
  -keyout "$KEY_FILE" \
  -out "$CERT_FILE" \
  -subj "/CN=${DOMAIN}/O=${DOMAIN}" \
  -addext "subjectAltName=DNS:${DOMAIN}"

echo "📦 Creating Kubernetes Secret '${SECRET_NAME}' in namespace '${NAMESPACE}' ..."

kubectl create secret tls "${SECRET_NAME}" \
  --cert="${CERT_FILE}" \
  --key="${KEY_FILE}" \
  --namespace="${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "✅ Secret '${SECRET_NAME}' đã được tạo/cập nhật."
echo ""
echo "Verify:"
kubectl get secret "${SECRET_NAME}" -n "${NAMESPACE}"
