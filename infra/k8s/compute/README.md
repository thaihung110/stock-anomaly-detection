# Compute Infrastructure

This directory manages compute resources for the data platform, primarily **Spark Operator** on Kubernetes.

## Directory Structure

### `/config`

- `spark.yaml` — Helm values for Spark Operator (overrides default chart values)
- `spark-serviceaccount-rbac.yaml` — ServiceAccount + RBAC for Spark applications

### `/debug`

Debug resources for troubleshooting:

- `spark_controller.yaml` — debug pod cho Spark Operator controller
- `gravitino-iceberg-debug-pod.yaml` — debug pod cho Gravitino Iceberg REST

### `/scripts`

Automation scripts for managing compute infrastructure.

### `/test_template`

Template output for testing Helm chart rendering without deploying:

- `spark_operator_template.yaml` — rendered Spark Operator manifests

---

## Cài đặt Spark Operator

### 1. Apply ServiceAccount và RBAC

Trước khi chạy bất kỳ SparkApplication nào, phải tạo ServiceAccount `spark` và RBAC permissions trong namespace `stock-anomaly-detection`:

```bash
kubectl apply -f config/spark-serviceaccount-rbac.yaml
```

File này tạo:
- **ServiceAccount** `spark` trong namespace `stock-anomaly-detection`
- **Role** `spark-app-role` với quyền quản lý pods, services, configmaps, PVC, events, và read secrets
- **RoleBinding** `spark-app-rolebinding` gắn role vào serviceaccount

> **Lưu ý:** Các SparkApplication manifest đều khai báo `serviceAccount: spark` trong driver spec. Nếu bỏ qua bước này, driver pod sẽ fail với lỗi `forbidden`.

### 2. Cài đặt Spark Operator

```bash
./scripts/install_spark_operators.sh
```

Lệnh này chạy:
```bash
helm upgrade --install openhouse-spark-operator helm/spark-operator -f config/spark.yaml
```

**Cấu hình chính trong `config/spark.yaml`:**

- **Controller**: 1 replica, 10 workers, info-level logging
- **Webhook**: enabled, port 9443, timeout 10s
- **Leader Election**: enabled cho controller và webhook
- **Prometheus Metrics**: enabled tại port 8080, endpoint `/metrics`
- **Cert Manager**: disabled (dùng self-signed certificates)

### 3. Gỡ cài đặt Spark Operator

```bash
./scripts/uninstall_spark_operators.sh
```

---

## Scripts Guide

| Script | Mô tả |
|--------|-------|
| `install_spark_operators.sh` | Cài/upgrade Spark Operator qua Helm |
| `uninstall_spark_operators.sh` | Xóa Spark Operator Helm release |
| `template_spark.sh` | Render manifests ra `test_template/` để preview |

---

## Kiểm tra sau khi cài đặt

```bash
# Kiểm tra Spark Operator đang chạy
kubectl get pods -n stock-anomaly-detection

# Kiểm tra ServiceAccount đã được tạo
kubectl get serviceaccount spark -n stock-anomaly-detection

# Kiểm tra RBAC
kubectl get role,rolebinding -n stock-anomaly-detection
```
