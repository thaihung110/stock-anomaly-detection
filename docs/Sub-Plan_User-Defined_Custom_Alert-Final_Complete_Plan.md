# Sub-Plan: User-Defined Custom Alert

## Finance Anomaly Detection Platform V3.3 — Final Complete Plan

> **Triết lý thiết kế:** PostgreSQL làm OLTP store cho alert rules, tái dùng tối đa stack hiện có (Kafka, FastStream, Telegram Bot, Spark). Không thêm service nào ngoài 1 PostgreSQL pod. Thiết kế đủ đầy để demo và mở rộng sau, không over-engineer.

---

## 1. Tính Năng Mang Lại Gì Cho End User?

Hệ thống hiện tại chỉ có **system-defined alerts**: platform tự quyết định khi nào anomaly đủ ngưỡng thì alert. User hoàn toàn thụ động — nhận gì hay nhận đó.

**Custom Alert giải quyết 3 vấn đề thực tế:**

**Problem 1 — Ngưỡng của platform không phải ngưỡng của tôi**

> Trader A chỉ quan tâm NVDA khi volume gấp 5x, không phải 3x như system default. Trader B theo dõi RSI chạm 25 — oversold theo chiến lược riêng. System alert không thể biết điều này.

**Problem 2 — Tôi chỉ muốn theo dõi một vài mã cụ thể**

> Long-term investor chỉ nắm AAPL, MSFT, NVDA. Không cần nhận alert của 497 mã còn lại. Custom alert cho phép pin symbols vào watchlist cá nhân.

**Problem 3 — Alert của platform đến quá muộn hoặc quá sớm với tôi**

> Với swing trader, alert khi `price_zscore > 2.5` là đủ để vào lệnh. Với risk manager bảo thủ hơn, cần `price_zscore > 4.0`. Một ngưỡng cứng không phục vụ được tất cả.

### User Scenarios

| Scenario | User             | Custom Rule                              | Value                                               |
| -------- | ---------------- | ---------------------------------------- | --------------------------------------------------- |
| **A**    | Swing Trader     | `NVDA: price CROSSES_UP 200`             | Biết chính xác khi breakout level quan trọng bị phá |
| **B**    | Quant Trader     | `*: volume_zscore > 4.5`                 | Scan toàn thị trường với ngưỡng tự chọn             |
| **C**    | Long Investor    | `[AAPL,MSFT,NVDA]: daily_return < -0.05` | Early warning chỉ cho danh mục cá nhân              |
| **D**    | Technical Trader | `AAPL: rsi_14 < 30`                      | Alert khi indicator đạt vùng hành động              |

---

## 2. Phạm Vi Tính Năng

### ✅ In Scope

- User tạo/sửa/xóa/tạm dừng alert rule qua **Telegram Bot commands**
- Hỗ trợ **alert types**: Price, Volume, Z-Score, Technical Indicator (RSI, Bollinger Band)
- Hỗ trợ **operators**: `>`, `<`, `>=`, `<=`, `CROSSES_UP`, `CROSSES_DOWN`
- **Single condition** per rule (1 field + 1 operator + 1 threshold)
- **Scope**: 1 symbol cụ thể, danh sách symbols, hoặc toàn thị trường (`*`)
- **Frequency**: `ONCE` (fire 1 lần rồi chuyển TRIGGERED) hoặc `EVERY_TIME`
- Delivery qua **Telegram** (tái dùng bot đã có)
- Lưu lịch sử alert fires vào **PostgreSQL** (queryable qua Telegram)
- **Batch sync** custom alert history vào Iceberg Gold Layer để analytics

### ❌ Out of Scope

- Composite conditions (AND/OR nhiều điều kiện)
- Email / Webhook delivery
- Hot-reload Kafka topic riêng
- React/Web UI
- Rate limiting / plan tiers

---

## 3. Alert Fields Hỗ Trợ

Tất cả fields lấy từ **`raw.stock.quotes` (Kafka)** kết hợp **`gold.rule_engine_context` (pre-loaded vào memory khi startup)**:

| Field              | Nguồn                     | Mô tả                                  | Operators phù hợp                                  |
| ------------------ | ------------------------- | -------------------------------------- | -------------------------------------------------- |
| `price`            | Kafka quote               | Giá hiện tại                           | `>`, `<`, `>=`, `<=`, `CROSSES_UP`, `CROSSES_DOWN` |
| `daily_return`     | Tính từ quote             | % thay đổi vs `prev_close`             | `>`, `<`, `>=`, `<=`                               |
| `day_volume`       | Kafka quote               | Volume tích lũy trong ngày             | `>`, `<`, `>=`, `<=`                               |
| `volume_zscore`    | Tính real-time từ context | `(volume - mean_20d) / std_20d`        | `>`, `<`, `>=`, `<=`                               |
| `volume_ratio_20d` | Tính real-time từ context | `volume / mean_volume_20d`             | `>`, `<`, `>=`, `<=`                               |
| `price_zscore`     | Tính real-time từ context | Return z-score 20d                     | `>`, `<`, `>=`, `<=`                               |
| `rsi_14`           | `rule_engine_context`     | RSI 14 ngày (daily, batch)             | `>`, `<`, `>=`, `<=`                               |
| `bb_position`      | `rule_engine_context`     | `(price-bb_lower)/(bb_upper-bb_lower)` | `>`, `<`, `>=`, `<=`                               |

> ⚠️ **Lưu ý quan trọng:** `rsi_14` và `bb_position` trong `rule_engine_context` là giá trị của ngày giao dịch trước (batch daily update lúc 07:00 UTC). Không phải real-time intraday. Alert message cần ghi rõ điều này để tránh nhầm lẫn cho user.

---

## 4. Data Model — PostgreSQL

### 4.1 ENUM Types

```sql
CREATE TYPE alert_operator AS ENUM (
    '>', '<', '>=', '<=', 'CROSSES_UP', 'CROSSES_DOWN'
);

CREATE TYPE alert_field AS ENUM (
    'price', 'daily_return', 'day_volume',
    'volume_zscore', 'volume_ratio_20d', 'price_zscore',
    'rsi_14', 'bb_position'
);

CREATE TYPE alert_status AS ENUM (
    'ACTIVE',       -- Đang chạy, evaluate mỗi quote
    'PAUSED',       -- User tạm dừng thủ công
    'TRIGGERED'     -- Đã fire xong (chỉ với frequency=ONCE), terminal state
);

CREATE TYPE alert_frequency AS ENUM (
    'ONCE',         -- Fire 1 lần → chuyển TRIGGERED
    'EVERY_TIME'    -- Fire mỗi lần condition match (subject to cooldown)
);
```

> **Tại sao dùng ENUM thay vì VARCHAR?** ENUM enforce valid values tại tầng DB — không có bug nào ở application layer có thể INSERT giá trị không hợp lệ. Ngoài ra PostgreSQL lưu ENUM hiệu quả hơn VARCHAR cho tập giá trị cố định.

### 4.2 Bảng `users`

```sql
CREATE TABLE users (
    user_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id   VARCHAR(50) UNIQUE NOT NULL,  -- Telegram chat_id
    username      VARCHAR(100),                 -- Telegram username (optional)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

> **Tại sao tách bảng `users` riêng?** Tránh coupling giữa business logic (alert rule) và delivery channel (Telegram). Nếu sau này thêm Web UI hay Email, `user_id` vẫn là UUID trung lập — không phải Telegram ID. Đây là nguyên tắc cơ bản của relational design.

### 4.3 Bảng `user_alert_rules`

```sql
CREATE TABLE user_alert_rules (
    rule_id         UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID             NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    rule_name       VARCHAR(200)     NOT NULL,

    -- Target scope
    symbols         TEXT[]           NOT NULL,
    -- Ví dụ: '{NVDA}' | '{AAPL,MSFT,NVDA}' | '{*}'
    -- '*' trong array nghĩa là scan toàn bộ symbols đang theo dõi

    -- Condition (single condition)
    field           alert_field      NOT NULL,
    operator        alert_operator   NOT NULL,
    threshold       DOUBLE PRECISION NOT NULL,
    threshold_upper DOUBLE PRECISION,
    -- NULL với tất cả operators hiện tại
    -- Dùng sau khi mở rộng INSIDE/OUTSIDE range operators
    -- Thiết kế phòng ngừa, không ảnh hưởng hiện tại

    -- Behavior
    frequency       alert_frequency  NOT NULL DEFAULT 'ONCE',
    cooldown_min    INTEGER          NOT NULL DEFAULT 60
                    CHECK (cooldown_min >= 0),
    -- Số phút tối thiểu giữa 2 lần fire liên tiếp
    -- Áp dụng cho EVERY_TIME; với ONCE thì không có ý nghĩa nhưng vẫn lưu

    -- State machine
    status          alert_status     NOT NULL DEFAULT 'ACTIVE',
    last_triggered  TIMESTAMPTZ,
    -- Giữ lại để Rule Engine kiểm tra cooldown nhanh
    -- Không maintain trigger_count thủ công (derive từ user_alert_events khi cần)

    -- Metadata
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rules_active  ON user_alert_rules(status)
    WHERE status = 'ACTIVE';                      -- Partial index: chỉ index ACTIVE rules
CREATE INDEX idx_rules_user    ON user_alert_rules(user_id);
CREATE INDEX idx_rules_symbols ON user_alert_rules USING GIN(symbols);
```

**State Machine của `status`:**

```
              /setalert
                  │
                  ▼
             [ACTIVE] ──── /pausealert ────► [PAUSED]
                │               ▲                │
                │               └─ /resumealert ─┘
                │
                │  frequency=ONCE & condition fires
                ▼
           [TRIGGERED]  ←── terminal, không tự chuyển ngược
                │
                └── /resetalert ──────────► [ACTIVE]

        Bất kỳ state nào ── /delalert ──► (DELETE row)
        EVERY_TIME rules KHÔNG BAO GIỜ vào TRIGGERED
```

### 4.4 Bảng `user_alert_events`

```sql
CREATE TABLE user_alert_events (
    event_id        UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id         UUID             NOT NULL
                    REFERENCES user_alert_rules(rule_id) ON DELETE CASCADE,
    user_id         UUID             NOT NULL,
    -- Denormalized từ rule để query history không cần JOIN

    -- Context tại thời điểm trigger
    symbol          VARCHAR(20)      NOT NULL,
    field           alert_field      NOT NULL,    -- Snapshot: field của rule lúc fire
    operator        alert_operator   NOT NULL,    -- Snapshot: operator của rule lúc fire
    threshold       DOUBLE PRECISION NOT NULL,    -- Snapshot: threshold của rule lúc fire
    field_value     DOUBLE PRECISION NOT NULL,    -- Actual value tại thời điểm trigger

    -- Timestamp
    triggered_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),

    -- Delivery
    delivered       BOOLEAN          NOT NULL DEFAULT FALSE,
    delivered_at    TIMESTAMPTZ,
    error_msg       TEXT                          -- NULL nếu delivery thành công
);

CREATE INDEX idx_events_rule ON user_alert_events(rule_id, triggered_at DESC);
CREATE INDEX idx_events_user ON user_alert_events(user_id, triggered_at DESC);
```

> **Tại sao snapshot `field`, `operator`, `threshold` vào events?** Events là lịch sử bất biến — nếu user sửa rule sau khi nó đã fire, history vẫn phản ánh đúng điều kiện tại thời điểm xảy ra. Đây là nguyên tắc event sourcing cơ bản.

> **Tại sao không có `trigger_count` trong `user_alert_rules`?** Counter thủ công dễ bị lệch khi có lỗi giữa chừng. Thay vào đó, derive từ events: `SELECT COUNT(*) FROM user_alert_events WHERE rule_id = ?`. Clean và luôn chính xác.

### 4.5 Tóm tắt quan hệ

```
users
  │ 1
  │
  │ N
user_alert_rules  ──── status: ACTIVE/PAUSED/TRIGGERED
  │ 1
  │
  │ N
user_alert_events ──── immutable history, snapshot context
```

---

## 5. Tương Thích Với Data Lakehouse Hiện Tại

### 5.1 Nguyên tắc tách biệt

PostgreSQL và Iceberg/Lakehouse là **hai hệ thống hoàn toàn độc lập**. Không có circular dependency:

|                   | PostgreSQL                            | Iceberg/MinIO                      |
| ----------------- | ------------------------------------- | ---------------------------------- |
| **Vai trò**       | OLTP — alert rules & events           | OLAP — historical data & analytics |
| **Query pattern** | Row-level lookup, INSERT/UPDATE nhanh | Batch analytical query             |
| **Ai đọc**        | Rule Engine, Telegram Bot             | Spark, Trino, Grafana, Superset    |
| **Ai ghi**        | Rule Engine, Telegram Bot             | Spark jobs                         |

### 5.2 Điểm giao duy nhất — `rule_engine_context` (Iceberg → In-Memory)

Rule Engine cần rolling stats (mean, std, rsi, bb...) từ `gold.rule_engine_context` (Iceberg) để tính `volume_zscore`, `price_zscore` real-time.

**Giải pháp:** Load toàn bộ `rule_engine_context` vào **in-memory dict** khi Rule Engine khởi động. PostgreSQL **không cần biết gì** về Iceberg.

```python
# Khi Rule Engine service start
context_cache: dict[str, dict] = load_rule_engine_context_from_iceberg()
# {symbol: {mean_volume_20d, std_volume_20d, rsi_14, bb_upper, bb_lower, ...}}
# Refresh daily sau khi Spark job build_rule_context chạy xong (07:00 UTC)
```

### 5.3 Batch Sync: PostgreSQL → Iceberg (cho Analytics)

Để Superset/Grafana dashboard hiển thị **cả system alerts lẫn custom alert fires** trong cùng một view, cần sync custom events vào Gold Layer.

**Thay đổi duy nhất trong Lakehouse:** Thêm 1 column `alert_source` vào `gold.fact_alert_history` hiện tại:

```sql
-- Thêm vào schema gold.fact_alert_history (Iceberg) hiện có
alert_source  VARCHAR(20)  DEFAULT 'system'
-- 'system'      = alert từ system anomaly detection (giá trị hiện tại)
-- 'user_custom' = alert từ user-defined rule
```

**Spark job `sync_custom_alerts` (daily, 07:30 UTC — sau `build_rule_context`):**

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("sync_custom_alerts").getOrCreate()

# Đọc từ PostgreSQL qua JDBC
custom_events = spark.read \
    .format("jdbc") \
    .option("url", "jdbc:postgresql://postgres:5432/alertdb") \
    .option("dbtable", """(
        SELECT e.event_id, e.rule_id, e.user_id,
               e.symbol, e.field, e.operator,
               e.threshold, e.field_value,
               e.triggered_at, e.delivered,
               r.rule_name, r.frequency
        FROM user_alert_events e
        JOIN user_alert_rules r ON e.rule_id = r.rule_id
        WHERE e.triggered_at >= CURRENT_DATE - INTERVAL '1 day'
          AND e.triggered_at <  CURRENT_DATE
    ) AS yesterday_events""") \
    .option("user", "postgres") \
    .option("password", "${PG_PASSWORD}") \
    .load()

# Map sang schema của gold.fact_alert_history
mapped = custom_events.selectExpr(
    "event_id        AS alert_id",
    "NULL            AS anomaly_id",      # Không có anomaly_id cho custom alerts
    "symbol",
    "triggered_at    AS alerted_at",
    "'telegram'      AS delivery_channel",
    "CASE WHEN delivered THEN 'DELIVERED' ELSE 'FAILED' END AS delivery_status",
    "'user_custom'   AS alert_source",
    "rule_name",
    "CONCAT(field, ' ', operator, ' ', threshold) AS rule_summary"
)

# Append vào Iceberg
mapped.write \
    .format("iceberg") \
    .mode("append") \
    .save("iceberg.gold.fact_alert_history")
```

### 5.4 Revised Architecture — Full Picture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REAL-TIME PATH                                │
│                                                                      │
│  [yfinance WebSocket] ──► Kafka: raw.stock.quotes                   │
│                                       │                              │
│                                       ▼                              │
│                          ┌─────────────────────────┐                │
│                          │   RULE ENGINE SERVICE    │                │
│                          │   (FastStream)           │                │
│                          │                          │                │
│  On startup:             │  in-memory:              │                │
│  ┌─────────────────┐     │  • context_cache         │                │
│  │ rule_engine_    │────►│    (from Iceberg)        │                │
│  │ context(Iceberg)│     │  • user_rules list       │                │
│  └─────────────────┘     │    (from PostgreSQL)     │                │
│                          │  • prev_prices dict      │                │
│  ┌─────────────────┐     │    (in-memory)           │                │
│  │ user_alert_     │────►│                          │                │
│  │ rules(PostgreSQL│     │  process_quote(event):   │                │
│  └─────────────────┘     │  ├─ System rules ──────────► Kafka:      │
│                          │  │  (anomaly detection)  │   alerts.      │
│                          │  │                       │   confirmed    │
│                          │  └─ User custom rules    │                │
│                          │     evaluate → fire?     │                │
│                          │     cooldown check ◄─────┼─ PostgreSQL   │
│                          │     Telegram notify      │                │
│                          │     INSERT events ──────►│─ PostgreSQL   │
│                          │     UPDATE rule status ──┼─► PostgreSQL  │
│                          └─────────────────────────┘                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        DAILY BATCH (07:00–07:30 UTC)                 │
│                                                                      │
│  07:00  Spark: build_rule_context ──► gold.rule_engine_context      │
│                                           │                          │
│  07:15  Rule Engine: reload context_cache ◄┘                        │
│                                                                      │
│  07:30  Spark: sync_custom_alerts                                    │
│           READ  PostgreSQL: user_alert_events (JDBC)                │
│           WRITE Iceberg: gold.fact_alert_history                    │
│                          alert_source = 'user_custom'               │
│                                                                      │
│  → Superset/Grafana thấy được cả system & custom alerts             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        USER INTERACTION                              │
│                                                                      │
│  Telegram Bot:                                                       │
│    /setalert → INSERT user_alert_rules (PostgreSQL)                  │
│             → POST /internal/reload-user-rules (Rule Engine)        │
│    /listalerts    → SELECT user_alert_rules (PostgreSQL)            │
│    /alerthistory  → SELECT user_alert_events (PostgreSQL)           │
│    /pausealert    → UPDATE status='PAUSED' (PostgreSQL)             │
│    /delalert      → DELETE user_alert_rules (PostgreSQL)            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Condition Evaluator

```python
prev_prices: dict[str, float] = {}  # In-memory, đủ cho đồ án

def get_field_value(quote: dict, field: str, ctx: dict) -> float | None:
    match field:
        case 'price':
            return quote['price']
        case 'daily_return':
            return (quote['price'] - quote['prev_close']) / quote['prev_close']
        case 'day_volume':
            return quote['day_volume']
        case 'volume_zscore':
            mean = ctx.get('mean_volume_20d', 0)
            std  = ctx.get('std_volume_20d', 1)
            return (quote['day_volume'] - mean) / max(std, 1)
        case 'volume_ratio_20d':
            mean = ctx.get('mean_volume_20d', 1)
            return quote['day_volume'] / max(mean, 1)
        case 'price_zscore':
            ret  = (quote['price'] - quote['prev_close']) / quote['prev_close']
            mean = ctx.get('mean_return_20d', 0)
            std  = ctx.get('std_return_20d', 0.001)
            return (ret - mean) / max(std, 0.001)
        case 'rsi_14':
            return ctx.get('rsi_14')       # daily, từ rule_engine_context
        case 'bb_position':
            upper = ctx.get('bb_upper_20d', 1)
            lower = ctx.get('bb_lower_20d', 0)
            return (quote['price'] - lower) / max(upper - lower, 0.001)
    return None


def evaluate_condition(
    current: float,
    prev: float | None,
    operator: str,
    threshold: float
) -> bool:
    match operator:
        case '>':           return current > threshold
        case '<':           return current < threshold
        case '>=':          return current >= threshold
        case '<=':          return current <= threshold
        case 'CROSSES_UP':
            if prev is None: return False
            return prev <= threshold < current
        case 'CROSSES_DOWN':
            if prev is None: return False
            return prev >= threshold > current
    return False


def check_cooldown(rule: dict, last_triggered_at) -> bool:
    """True = còn trong cooldown, SKIP."""
    if last_triggered_at is None:
        return False
    elapsed = (datetime.utcnow() - last_triggered_at).total_seconds() / 60
    return elapsed < rule['cooldown_min']
```

---

## 7. Telegram Bot Interface

### Commands

```
/setalert <SYMBOL|*> <field> <operator> <threshold> [once|every]

Ví dụ:
  /setalert NVDA price CROSSES_UP 200
  /setalert AAPL rsi_14 < 30
  /setalert AAPL rsi_14 < 30 once
  /setalert * volume_zscore > 4.5 every
  /setalert MSFT daily_return < -0.05

/listalerts               → Danh sách rules hiện tại (ACTIVE + PAUSED)
/pausealert <rule_id>     → Tạm dừng rule
/resumealert <rule_id>    → Kích hoạt lại rule
/resetalert <rule_id>     → Reset TRIGGERED → ACTIVE
/delalert <rule_id>       → Xóa rule
/alerthistory [SYMBOL]    → 10 fires gần nhất
```

### Alert Message Format

```
🔔 CUSTOM ALERT — NVDA
━━━━━━━━━━━━━━━━━━━━━
Rule: "NVDA price CROSSES_UP 200"

✅ price vừa vượt 200.0
   Giá hiện tại: $201.52
   Prev price:   $199.80

📊 Snapshot:
   Volume ratio:  2.1x avg
   Volume Z-score: +1.9σ
   RSI(14): 68.2  ⚠️ (daily, not real-time)
━━━━━━━━━━━━━━━━━━━━━
[/pausealert abc123]  [/delalert abc123]
```

---

## 8. Tích Hợp Vào Stack Hiện Tại

### Những gì cần thêm / sửa

| Thành phần                  | Thay đổi                                                      | Effort               |
| --------------------------- | ------------------------------------------------------------- | -------------------- |
| **PostgreSQL**              | Deploy 1 pod `postgres:15-alpine`, 256MB RAM, PVC 1GB         | Setup 1 lần, ~1 ngày |
| **Rule Engine**             | Thêm ~150 dòng Python vào FastStream consumer hiện có         | 2–3 ngày             |
| **Telegram Bot**            | Thêm command handlers, `/internal/reload-user-rules` endpoint | 2–3 ngày             |
| **Spark**                   | Thêm job `sync_custom_alerts` (JDBC → Iceberg)                | 1 ngày               |
| **gold.fact_alert_history** | Thêm 1 column `alert_source VARCHAR(20)`                      | 1 giờ                |
| **Kafka**                   | Không thay đổi gì                                             | 0                    |
| **Iceberg schema khác**     | Không thay đổi gì                                             | 0                    |

### PostgreSQL Deployment (GKE)

```yaml
resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"
# Image: postgres:15-alpine (~80MB)
# PVC: 1Gi Standard storage
```

---

## 9. Lộ Trình Triển Khai

### Phase 1 — PostgreSQL Setup & Data Model (1–2 ngày)

- Deploy PostgreSQL pod lên GKE
- Tạo ENUM types, bảng `users`, `user_alert_rules`, `user_alert_events`
- Viết `postgres_client.py`: `load_active_rules()`, `insert_event()`, `update_rule_status()`, `check_cooldown_from_db()`
- Thêm column `alert_source` vào `gold.fact_alert_history` (Iceberg schema migration)

### Phase 2 — Rule Engine Extension (2–3 ngày)

- Implement `get_field_value()` + `evaluate_condition()` với đầy đủ operators
- Tích hợp vào `process_quote()` trong FastStream consumer hiện có
- Implement in-memory `user_rules` list + `prev_prices` dict
- Implement `POST /internal/reload-user-rules` HTTP endpoint
- Unit test toàn bộ operators với mock data

### Phase 3 — Telegram Bot Commands (2–3 ngày)

- Implement command parser cho `/setalert` với validation
- Implement `/listalerts`, `/pausealert`, `/resumealert`, `/resetalert`, `/delalert`, `/alerthistory`
- Format Telegram alert message
- E2E test: gõ `/setalert` → đợi condition match → nhận Telegram alert

### Phase 4 — Batch Sync & Analytics (1 ngày)

- Implement Spark job `sync_custom_alerts` (JDBC → Iceberg)
- Thêm vào Airflow/scheduler cùng cụm daily jobs (07:30 UTC)
- Verify Superset hiển thị được `alert_source = 'user_custom'` trong dashboard

### Tổng: ~7–9 ngày làm việc

---

## 10. Chi Phí Bổ Sung

| Hạng mục                                   | $/tháng            | Ghi chú                               |
| ------------------------------------------ | ------------------ | ------------------------------------- |
| PostgreSQL pod (GKE, `postgres:15-alpine`) | ~$5–8              | 256MB RAM, chạy trong cluster hiện có |
| PersistentDisk 1GB                         | ~$0.10             | Lưu PostgreSQL data                   |
| JDBC driver Spark (Spark → PostgreSQL)     | $0                 | Open source                           |
| **Tổng bổ sung**                           | **~$6–9/tháng**    |                                       |
| **Tổng platform (bao gồm custom alert)**   | **~$96–100/tháng** | Nằm trong budget                      |
