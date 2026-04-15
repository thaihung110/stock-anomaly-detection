#!/bin/bash
# =============================================================================
# setup_gravitino_warehouses.sh
#
# Script tạo Metalake và các Warehouse (Iceberg Catalogs) trong Gravitino.
# S3 config (bucket, endpoint, credentials) được truyền PER-CATALOG ở đây,
# KHÔNG nằm trong values.yaml.
#
# Mỗi Catalog = 1 Warehouse = 1 S3 Bucket.
# Tạo bao nhiêu warehouse tùy ý, không cần restart Gravitino.
# =============================================================================

set -euo pipefail

# ── CẤU HÌNH ────────────────────────────────────────────────────────────────
# Gravitino API endpoint — dùng ingress URL khi chạy từ ngoài cluster
# HTTP (không cần cert), HTTPS cần thêm -k hoặc trust CA cert
GRAVITINO_ENDPOINT="${GRAVITINO_ENDPOINT:-http://openhouse.gravitino.test}"

# Tên metalake — khớp với dynamicConfigProvider.metalake trong gravitino.yaml
METALAKE_NAME="${METALAKE_NAME:-my-metalake}"

# PostgreSQL dùng làm Iceberg TABLE METADATA backend (dùng chung cho tất cả catalogs)
# Đây là DB lưu Iceberg schemas/snapshots/manifests — KHÁC với DB entity store của Gravitino
ICEBERG_JDBC_HOST="${ICEBERG_JDBC_HOST:-openhouse-postgresql-primary}"
ICEBERG_JDBC_DB="${ICEBERG_JDBC_DB:-iceberg_catalog}"
ICEBERG_JDBC_USER="${ICEBERG_JDBC_USER:-iceberg}"
ICEBERG_JDBC_PASSWORD="${ICEBERG_JDBC_PASSWORD:-iceberg}"

# MinIO endpoint (K8s service name trong cùng cluster/namespace)
S3_ENDPOINT="${S3_ENDPOINT:-http://openhouse-minio:9000}"
S3_REGION="${S3_REGION:-us-east-1}"

# MinIO credentials cho từng bucket
# bronze bucket — dùng spark-data-user (đã tạo bởi setup_minio_users.sh)
MINIO_BRONZE_ACCESS_KEY="${MINIO_BRONZE_ACCESS_KEY:-spark-data-user}"
MINIO_BRONZE_SECRET_KEY="${MINIO_BRONZE_SECRET_KEY:-spark-data-user}"

# silver bucket — dùng spark-silver-user (cần tạo qua setup_minio_users.sh)
MINIO_SILVER_ACCESS_KEY="${MINIO_SILVER_ACCESS_KEY:-spark-silver-user}"
MINIO_SILVER_SECRET_KEY="${MINIO_SILVER_SECRET_KEY:-spark-silver-user}"
# ─────────────────────────────────────────────────────────────────────────────

# Màu sắc output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}   $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERR]${NC}  $*"; }

# ── HELPER: gọi Gravitino API ────────────────────────────────────────────────
gravitino_api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"

  local response
  local http_code

  if [ -n "$body" ]; then
    response=$(curl -s -w "\n%{http_code}" \
      -X "$method" \
      -H "Content-Type: application/json" \
      -H "Accept: application/vnd.gravitino.v1+json" \
      -d "$body" \
      "${GRAVITINO_ENDPOINT}${path}")
  else
    response=$(curl -s -w "\n%{http_code}" \
      -X "$method" \
      -H "Accept: application/vnd.gravitino.v1+json" \
      "${GRAVITINO_ENDPOINT}${path}")
  fi

  http_code=$(echo "$response" | tail -n1)
  body_response=$(echo "$response" | head -n -1)

  if [[ "$http_code" -ge 400 ]]; then
    log_error "API ${method} ${path} failed (HTTP ${http_code}): ${body_response}"
    return 1
  fi

  echo "$body_response"
}

# ── STEP 1: Kiểm tra Gravitino server ────────────────────────────────────────
log_info "Checking Gravitino server at ${GRAVITINO_ENDPOINT} ..."
if ! curl -sf "${GRAVITINO_ENDPOINT}/api/version" > /dev/null; then
  log_error "Gravitino server is not reachable. Check port-forward or service."
  exit 1
fi
log_success "Gravitino server is up."

# ── STEP 2: Tạo Metalake (idempotent) ────────────────────────────────────────
log_info "Creating metalake '${METALAKE_NAME}' ..."
set +e
gravitino_api GET "/api/metalakes/${METALAKE_NAME}" > /dev/null 2>&1
METALAKE_EXISTS=$?
set -e

if [ "$METALAKE_EXISTS" -eq 0 ]; then
  log_warn "Metalake '${METALAKE_NAME}' already exists, skipping."
else
  gravitino_api POST "/api/metalakes" \
    "{\"name\": \"${METALAKE_NAME}\", \"comment\": \"Main Data Lake\", \"properties\": {}}" \
    > /dev/null
  log_success "Metalake '${METALAKE_NAME}' created."
fi

# ── HELPER: Tạo 1 warehouse (Iceberg Catalog) ────────────────────────────────
# Arguments:
#   $1 = catalog_name    (tên warehouse, ví dụ: "bronze")
#   $2 = s3_bucket       (tên bucket MinIO, ví dụ: "bronze")
#   $3 = s3_access_key   (MinIO access key)
#   $4 = s3_secret_key   (MinIO secret key)
#   $5 = s3_path_prefix  (prefix trong bucket, mặc định: "warehouse")
#   $6 = iceberg_db      (PostgreSQL DB, mặc định: dùng ICEBERG_JDBC_DB chung)
# ─────────────────────────────────────────────────────────────────────────────
create_warehouse() {
  local catalog_name="$1"
  local s3_bucket="$2"
  local s3_access_key="$3"
  local s3_secret_key="$4"
  local s3_path_prefix="${5:-warehouse}"
  local iceberg_db="${6:-${ICEBERG_JDBC_DB}}"

  local warehouse_path="s3://${s3_bucket}/${s3_path_prefix}"
  local jdbc_uri="jdbc:postgresql://${ICEBERG_JDBC_HOST}:5432/${iceberg_db}"

  log_info "Creating warehouse catalog '${catalog_name}' → ${warehouse_path} ..."

  # Kiểm tra xem catalog đã tồn tại chưa (idempotent)
  set +e
  gravitino_api GET "/api/metalakes/${METALAKE_NAME}/catalogs/${catalog_name}" > /dev/null 2>&1
  CATALOG_EXISTS=$?
  set -e

  if [ "$CATALOG_EXISTS" -eq 0 ]; then
    log_warn "Catalog '${catalog_name}' already exists, skipping."
    return 0
  fi

  # Payload tạo catalog với đầy đủ S3 config
  # S3 credentials được định nghĩa per-catalog — mỗi bucket dùng credential riêng
  local payload
  payload=$(cat <<EOF
{
  "name": "${catalog_name}",
  "type": "RELATIONAL",
  "provider": "lakehouse-iceberg",
  "comment": "Iceberg warehouse on s3://${s3_bucket}",
  "properties": {
    "catalog-backend":      "jdbc",
    "uri":                  "${jdbc_uri}",
    "jdbc-user":            "${ICEBERG_JDBC_USER}",
    "jdbc-password":        "${ICEBERG_JDBC_PASSWORD}",
    "jdbc-driver":          "org.postgresql.Driver",
    "jdbc-initialize":      "true",

    "warehouse":            "${warehouse_path}",
    "io-impl":              "org.apache.iceberg.aws.s3.S3FileIO",

    "s3-access-key-id":     "${s3_access_key}",
    "s3-secret-access-key": "${s3_secret_key}",
    "s3-endpoint":          "${S3_ENDPOINT}",
    "s3-region":            "${S3_REGION}",
    "s3-path-style-access": "true"
  }
}
EOF
)

  gravitino_api POST "/api/metalakes/${METALAKE_NAME}/catalogs" "$payload" > /dev/null
  log_success "Warehouse '${catalog_name}' created → ${warehouse_path}"
}

# =============================================================================
# ── STEP 3: TẠO CÁC WAREHOUSE ────────────────────────────────────────────────
# Format: create_warehouse <catalog_name> <s3_bucket> <access_key> <secret_key> [path_prefix] [iceberg_db]
# =============================================================================

echo ""
log_info "Creating warehouses..."
echo ""

# Bronze warehouse → bucket "bronze" trên MinIO
create_warehouse \
  "bronze" \
  "bronze" \
  "${MINIO_BRONZE_ACCESS_KEY}" \
  "${MINIO_BRONZE_SECRET_KEY}"

echo ""

# Silver warehouse → bucket "silver" trên MinIO
create_warehouse \
  "silver" \
  "silver" \
  "${MINIO_SILVER_ACCESS_KEY}" \
  "${MINIO_SILVER_SECRET_KEY}"

echo ""
log_success "All warehouses have been set up."
echo ""
echo "================================================="
echo " Verify:"
echo "   curl http://openhouse.gravitino.test/api/metalakes/${METALAKE_NAME}/catalogs"
echo ""
echo " Spark config (Iceberg REST catalog):"
echo "   spark.sql.catalog.gravitino.type      = rest"
echo "   spark.sql.catalog.gravitino.uri        = http://openhouse.gravitino-iceberg.test"
echo "   spark.sql.catalog.gravitino.warehouse  = ${METALAKE_NAME}.bronze"
echo "================================================="
