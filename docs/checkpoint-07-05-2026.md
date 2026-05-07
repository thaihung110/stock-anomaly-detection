# Checkpoint 7/5/2026

## Những gì đã làm

### Hạ tầng Kubernetes

**Storage layer — đã deploy:**

- **MinIO** — object storage S3-compatible, lưu Iceberg data files và Spark checkpoints
- **Gravitino** — Iceberg REST catalog (custom Helm chart), xác thực OAuth2 qua Keycloak
- **Keycloak** — identity provider, cấp token cho Spark và Gravitino; TLS tự cấp

**Compute layer — đã deploy:**

- **Spark Operator** (kubeflow) — quản lý SparkApplication CRD trên K8s
- **Trino** — query engine trên Iceberg

**Orchestration layer — đã deploy:**

- **Kafka** — streaming backbone, 5 topics: `raw.stock.quotes`, `raw.stock.trades`, `raw.stock.news`, `alerts.raw`, `alerts.confirmed`
- **Airflow** — batch orchestration, tích hợp Keycloak SSO

---

### Spark Applications (Scala)

| App                           | Layer       | Loại      | Trạng thái  |
| ----------------------------- | ----------- | --------- | ----------- |
| `ohlcv-daily-loader`          | Bronze      | Batch     | ✅ Done     |
| `company-info-loader`         | Bronze      | Batch     | ✅ Done     |
| `news-ingest-stream`          | Bronze      | Streaming | ✅ Done     |
| `ohlcv-daily-cleaner`         | Silver      | Batch     | ✅ Done     |
| `news-cleaner`                | Silver      | Batch     | ✅ Done     |
| `trades-ohlcv-stream`         | Silver      | Streaming | ✅ Done     |
| `dim-loader`                  | Gold        | Batch     | ✅ Done     |
| `fact-ohlcv-daily-builder`    | Gold        | Batch     | ✅ Done     |
| `rule-engine-context-builder` | Gold        | Batch     | ✅ Done     |
| `sync-custom-alerts`          | Gold/Bridge | Batch     | ❌ Chưa làm |

Tất cả app kết nối Iceberg qua Gravitino REST + MinIO (S3A), đóng fat-JAR bằng `sbt assembly`, deploy qua SparkApplication CRD. K8s manifests tập trung tại `spark-application/k8s/`.

---

### Airflow DAGs

| DAG                                     | Schedule           | Nội dung                                       |
| --------------------------------------- | ------------------ | ---------------------------------------------- |
| `spark_ohlcv_daily_pipeline`            | Daily 06:00 UTC    | loader → cleaner → fact builder → rule context |
| `spark_news_daily_pipeline`             | Daily 06:00 UTC    | news-cleaner                                   |
| `spark_batch_weekly_dimension_pipeline` | Chủ nhật 05:00 UTC | company-info-loader → dim-loader               |

DAG tự implement `SparkLifecycleTrigger` (async poll CRD), `DictSparkKubernetesOperator` (submit từ dict), TimeSensor enforce thứ tự job, auto-cleanup khi fail.

---

### Data Producers (Python)

| Service                    | Mô tả                                        |
| -------------------------- | -------------------------------------------- |
| `finnhub-trades-producer`  | Finnhub WebSocket → Kafka `raw.stock.trades` |
| `finnhub-news-producer`    | Finnhub REST poll → Kafka `raw.stock.news`   |
| `yfinance-quotes-producer` | yfinance REST → Kafka `raw.stock.quotes`     |

Code hoàn chỉnh, có Dockerfile. Chưa có K8s deployment manifests.

---

## Tiến độ

| Module                                    | %    |
| ----------------------------------------- | ---- |
| Hạ tầng K8s                               | ~90% |
| Spark Batch Pipeline (Bronze→Silver→Gold) | ~90% |
| Spark Streaming                           | 100% |
| Airflow DAGs                              | ~85% |
| Data Producers                            | ~80% |
| Rule Engine service                       | 0%   |
| Alert Service (Telegram)                  | 0%   |
| Telegram Bot + Custom Alert               | 0%   |

**Tổng thể: ~60%**

> LLM Agent sẽ được triển khai ở giai đoạn sau, không tính vào scope hiện tại.

---

## Tasks tiếp theo

### 1. Hoàn thiện pipeline (còn thiếu)

1. PostgreSQL schema setup — tạo các bảng OLTP cho custom alert: `users`, `user_alert_rules`, `user_alert_events`, `sync_watermarks`
2. `sync-custom-alerts` Spark job — đọc watermark từ `sync_watermarks`, sync `user_alert_events` → `gold.fact_alert_history` (Iceberg), cập nhật watermark sau khi ghi thành công
3. Airflow DAG cho `sync-custom-alerts` — schedule 07:30 UTC, độc lập với OHLCV pipeline
4. K8s deployment manifests cho 3 Python producers

### 2. Rule Engine (ưu tiên cao)

5. Rule Engine core (FastStream) — consume `raw.stock.quotes`, load context từ `gold.rule_engine_context` lúc khởi động, áp dụng 6 rules, publish `alerts.raw`
6. Expose `POST /internal/reload-user-rules` — hot-reload user rules từ PostgreSQL không cần restart
7. Custom rule evaluator — `get_field_value()` cho 8 fields (`price`, `daily_return`, `day_volume`, `volume_zscore`, `volume_ratio_20d`, `price_zscore`, `rsi_14`, `bb_position`) + `evaluate_condition()` cho 6 operators kể cả `CROSSES_UP` / `CROSSES_DOWN`

### 3. Alert Service

8. Alert Service (FastStream) — consume `alerts.confirmed`, format message, gửi Telegram Bot API, ghi log vào `gold.fact_alert_history`

### 4. Telegram Bot + Custom Alert

9. Telegram Bot — xử lý các commands: `/setalert`, `/listalerts`, `/pausealert`, `/resumealert`, `/resetalert`, `/delalert`, `/alerthistory`
10. On `/setalert`: INSERT vào PostgreSQL → gọi `POST /internal/reload-user-rules` để Rule Engine hot-reload ngay

### 5. Hoàn thiện còn lại

11. Integration tests: quote → `alerts.raw`; custom rule fire → PostgreSQL `user_alert_events` inserted
12. `docs/oltp-olap-bridge.md` — bridge contract giữa PostgreSQL và Iceberg
