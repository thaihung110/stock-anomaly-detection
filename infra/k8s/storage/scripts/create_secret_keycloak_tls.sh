#!/bin/bash

set -eu

APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "${APP_DIR}" || exit 1

function create_tls {
  local domain=$1
  local key=$2
  local cert=$3

  # Create tls directory if it doesn't exist
  mkdir -p "$(dirname "$key")"

  # Generate a self-signed certificate with SAN (Subject Alternative Name)
  openssl req -x509 -nodes -days 10000 \
    -newkey rsa:2048 \
    -keyout "$key" \
    -out "$cert" \
    -subj "/CN=${domain}/O=${domain}" \
    -addext "subjectAltName=DNS:${domain}"
}

function main {
  local domain="openhouse.keycloak.test"
  local key="tls/keycloak_tls.key"
  local cert="tls/keycloak_tls.cert"
  local secret_name="keycloak-catalog-tls"

  echo "Generating TLS certificate for ${domain} ..."

  # Create TLS certificate
  create_tls "$domain" "$key" "$cert"

  # Delete existing secret if it exists
  if kubectl get secret "$secret_name" &>/dev/null; then
    echo "Secret $secret_name already exists. Deleting..."
    kubectl delete secret "$secret_name"
  fi

  # Create new secret
  kubectl create secret tls "$secret_name" \
    --cert="$cert" \
    --key="$key"

  echo "✅ Secret $secret_name created successfully!"
}

main
