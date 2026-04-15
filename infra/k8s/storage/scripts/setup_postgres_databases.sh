#!/bin/bash
# =============================================================================
# setup_postgres_databases.sh
#
# Tạo trực tiếp các database và user trên PostgreSQL đang chạy trong K8s.
# Dùng khi không thể reinstall PostgreSQL (đang có dữ liệu).
#
# PostgreSQL primary pod: openhouse-postgresql-primary-0
# =============================================================================

set -euo pipefail

# ── CẤU HÌNH ─────────────────────────────────────────────────────────────────
PG_POD="${PG_POD:-openhouse-postgresql-primary-0}"
PG_NAMESPACE="${PG_NAMESPACE:-default}"
PG_ADMIN_USER="postgres"
# Password của postgres admin — lấy từ secrets trong cluster
# (trùng với auth.postgresPassword trong postgresql.yaml)
PG_ADMIN_PASSWORD="${PG_ADMIN_PASSWORD:-admin}"
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}   $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERR]${NC}  $*"; }

# Helper: chạy SQL trong pod
run_sql() {
  local sql="$1"
  kubectl exec -n "${PG_NAMESPACE}" "${PG_POD}" -- \
    env PGPASSWORD="${PG_ADMIN_PASSWORD}" \
    psql -U "${PG_ADMIN_USER}" -c "$sql" 2>&1
}

# Helper: tạo database + user nếu chưa tồn tại (idempotent)
create_db_and_user() {
  local db_name="$1"
  local user_name="$2"
  local user_password="$3"
  local description="${4:-$db_name}"

  log_info "Setting up database '${db_name}' for ${description}..."

  # Tạo user nếu chưa có
  local user_exists
  user_exists=$(kubectl exec -n "${PG_NAMESPACE}" "${PG_POD}" -- \
    env PGPASSWORD="${PG_ADMIN_PASSWORD}" \
    psql -U "${PG_ADMIN_USER}" -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname='${user_name}';" 2>/dev/null || echo "")

  if [ "${user_exists}" = "1" ]; then
    log_warn "  User '${user_name}' already exists — updating password."
    run_sql "ALTER USER ${user_name} WITH ENCRYPTED PASSWORD '${user_password}';" > /dev/null
  else
    run_sql "CREATE USER ${user_name} WITH ENCRYPTED PASSWORD '${user_password}';" > /dev/null
    log_success "  User '${user_name}' created."
  fi

  # Tạo database nếu chưa có
  local db_exists
  db_exists=$(kubectl exec -n "${PG_NAMESPACE}" "${PG_POD}" -- \
    env PGPASSWORD="${PG_ADMIN_PASSWORD}" \
    psql -U "${PG_ADMIN_USER}" -tAc \
    "SELECT 1 FROM pg_database WHERE datname='${db_name}';" 2>/dev/null || echo "")

  if [ "${db_exists}" = "1" ]; then
    log_warn "  Database '${db_name}' already exists — skipping creation."
  else
    run_sql "CREATE DATABASE ${db_name};" > /dev/null
    log_success "  Database '${db_name}' created."
  fi

  # Set ownership
  run_sql "ALTER DATABASE ${db_name} OWNER TO ${user_name};" > /dev/null
  log_success "  Database '${db_name}' owner set to '${user_name}'."
}

# ── STEP 1: Kiểm tra kết nối ─────────────────────────────────────────────────
log_info "Checking connection to PostgreSQL pod '${PG_POD}' in namespace '${PG_NAMESPACE}'..."
if ! kubectl exec -n "${PG_NAMESPACE}" "${PG_POD}" -- \
    env PGPASSWORD="${PG_ADMIN_PASSWORD}" \
    psql -U "${PG_ADMIN_USER}" -c "SELECT 1;" > /dev/null 2>&1; then
  log_error "Cannot connect to PostgreSQL. Check pod name, namespace and admin password."
  echo ""
  echo "Current postgres pods:"
  kubectl get pods -n "${PG_NAMESPACE}" | grep postgres
  exit 1
fi
log_success "Connected to PostgreSQL successfully."
echo ""

# ── STEP 2: Tạo các database ──────────────────────────────────────────────────
# Format: create_db_and_user <db_name> <user_name> <password> <description>

create_db_and_user \
  "gravitino_db" \
  "gravitino" \
  "gravitino" \
  "Gravitino entity store (metalake/catalog definitions)"

echo ""

create_db_and_user \
  "iceberg_catalog" \
  "iceberg" \
  "iceberg" \
  "Iceberg table metadata backend (schemas/snapshots)"

echo ""
log_success "All databases created successfully!"
echo ""
echo "Connection strings for gravitino.yaml:"
echo "  entity.jdbcUrl:          jdbc:postgresql://openhouse-postgresql-primary:5432/gravitino_db"
echo "  entity.jdbcUser:         gravitino"
echo "  entity.jdbcPassword:     gravitino"
echo ""
echo "  icebergRest static uri:  jdbc:postgresql://openhouse-postgresql-primary:5432/iceberg_catalog"
echo "  (or per-catalog when using dynamic mode — passed in API properties)"
echo ""
echo "Verify with:"
echo "  kubectl exec -n ${PG_NAMESPACE} ${PG_POD} -- env PGPASSWORD=${PG_ADMIN_PASSWORD} psql -U ${PG_ADMIN_USER} -c '\\l'"
