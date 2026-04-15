# Innovation Note — Hoàn thiện tích hợp OLTP và Lakehouse cho Custom Alert

## 1. Mục tiêu cải tiến

Thiết kế hiện tại của hệ thống đã đi đúng hướng khi tách rõ:

- **PostgreSQL** cho phần **OLTP**: quản lý `users`, `user_alert_rules`, `user_alert_events`
- **Iceberg/MinIO Lakehouse** cho phần **OLAP**: phục vụ analytics và dashboard, đặc biệt là `gold.fact_alert_history`
- **Spark job** làm lớp bridge để đồng bộ dữ liệu từ OLTP sang Lakehouse

Kiến trúc này là hợp lý và đúng bản chất của các hệ thống dữ liệu hiện đại: operational workload và analytical workload được tách riêng để mỗi lớp tối ưu đúng vai trò của nó.

Tuy nhiên, để thiết kế trở nên “chuẩn chỉ” và production-friendly hơn, phần tích hợp giữa OLTP và Lakehouse nên được hoàn thiện theo **2 cải tiến bổ sung** dưới đây.

---

## 2. Cải tiến 1 — Watermark cho incremental sync

### Bối cảnh

Trong thiết kế ban đầu, Spark job sync dữ liệu từ PostgreSQL sang Iceberg theo cửa sổ thời gian cố định theo ngày:

```sql
SELECT ...
FROM user_alert_events
WHERE triggered_at >= CURRENT_DATE - INTERVAL '1 day'
  AND triggered_at < CURRENT_DATE;
```

Cách này hoạt động được, nhưng có nhược điểm:

- Có thể **bỏ sót event** nếu job chạy trễ, rerun, hoặc thay đổi lịch chạy.
- Không thể hiện rõ hệ thống đã sync đến mốc nào.
- Logic đồng bộ phụ thuộc vào boundary theo ngày, không thật sự robust.

### Giải pháp

Thêm một bảng watermark nhỏ trong PostgreSQL để lưu **mốc sync cuối cùng** của job.

#### Bảng `sync_watermarks`

```sql
CREATE TABLE sync_watermarks (
    job_name     VARCHAR(50) PRIMARY KEY,
    last_sync_at TIMESTAMPTZ NOT NULL
);

INSERT INTO sync_watermarks (job_name, last_sync_at)
VALUES ('custom_alerts_to_iceberg', '1970-01-01 00:00:00+00');
```

- `job_name`: tên job đồng bộ
- `last_sync_at`: thời điểm cuối cùng đã sync thành công

#### Logic incremental mới

1. Spark job đọc `last_sync_at` từ `sync_watermarks`
2. Query PostgreSQL lấy toàn bộ `user_alert_events` có `triggered_at > last_sync_at`
3. Append vào `gold.fact_alert_history` với `alert_source = 'user_custom'`
4. Nếu ghi Iceberg thành công, cập nhật lại `last_sync_at`

### Giá trị mang lại

- Không bỏ sót dữ liệu khi job chạy lệch lịch
- Dễ rerun và dễ debug
- Phù hợp với incremental ETL pattern chuẩn trong hệ thống bridge OLTP → OLAP
- Không làm tăng complexity đáng kể

---

## 3. Cải tiến 2 — Thiết lập OLTP–OLAP Bridge Contract rõ ràng

### Vấn đề

Hiện tại, việc đồng bộ từ `user_alert_events` (PostgreSQL) sang `gold.fact_alert_history` (Iceberg) mới dừng ở mức “có sync dữ liệu”.

Điểm còn thiếu là một **bridge contract** rõ ràng, tức là tài liệu hóa và chuẩn hóa việc:

- Bảng nào là **source of truth**
- Bảng nào là **analytics sink**
- Mapping cột nguồn → cột đích như thế nào
- Latency kỳ vọng là bao lâu
- Chính sách incremental sync là gì
- Trường hợp nào được phép transform, trường hợp nào chỉ copy nguyên dạng

Nếu không có contract này, ETL sẽ trở thành một lớp tích hợp “ngầm hiểu”, dễ gây schema drift, khó maintain, và khó bảo vệ khi trình bày design.

### Giải pháp

Bổ sung một tài liệu thiết kế riêng, ví dụ:

```text
/docs/oltp-olap-bridge.md
```

Tài liệu này mô tả rõ bridge giữa PostgreSQL và Lakehouse cho phần custom alert.

### Nội dung bắt buộc của bridge contract

#### 1. Nguồn và đích dữ liệu

- **Nguồn OLTP**: `user_alert_events` trong PostgreSQL
- **Đích OLAP**: `gold.fact_alert_history` trong Iceberg

#### 2. Data ownership

- PostgreSQL là **system of record** cho custom alert runtime data
- Iceberg chỉ là **analytical copy** phục vụ dashboard, BI, historical analysis
- Không có chiều ghi ngược từ Iceberg về PostgreSQL

#### 3. Column mapping

Ví dụ contract mapping:

| PostgreSQL `user_alert_events`   | Iceberg `gold.fact_alert_history`      | Ý nghĩa                    |
| -------------------------------- | -------------------------------------- | -------------------------- |
| `event_id`                       | `alert_id`                             | ID duy nhất của alert fire |
| `rule_id`                        | `rule_id` hoặc field mở rộng           | Liên kết về rule gốc       |
| `user_id`                        | `user_id` hoặc dimension key           | User tạo alert             |
| `symbol`                         | `symbol` / `symbol_key`                | Mã cổ phiếu được trigger   |
| `triggered_at`                   | `alerted_at`                           | Thời điểm alert fire       |
| `delivered`                      | `delivery_status`                      | Trạng thái gửi Telegram    |
| `field`, `operator`, `threshold` | `rule_summary` hoặc structured columns | Snapshot điều kiện rule    |
| hằng số                          | `alert_source = 'user_custom'`         | Phân biệt với system alert |

#### 4. Incremental sync policy

- Sync theo **watermark**, không sync theo ngày cứng
- Chỉ đồng bộ bản ghi mới từ PostgreSQL sang Iceberg
- Iceberg đóng vai trò append-only analytical history

#### 5. Freshness / latency expectation

- Đây là **analytics pipeline**, không phải transactional path
- Dashboard chấp nhận độ trễ theo lịch batch (ví dụ daily)
- Real-time alert delivery vẫn đi trực tiếp từ Rule Engine → Telegram, không phụ thuộc Lakehouse

### Giá trị mang lại

- Design rõ ràng hơn về mặt kiến trúc
- Tránh nhập nhằng vai trò giữa OLTP và OLAP
- Dễ maintain nếu sau này đổi schema
- Dễ giải thích trong luận văn, slide bảo vệ, và phần system design document

---

## 4. Plan triển khai theo đề xuất 3.2

Để áp dụng cải tiến “OLTP–OLAP Bridge Contract”, có thể triển khai theo plan ngắn gọn sau.

### Phase 1 — Chuẩn hóa khái niệm dữ liệu

- Xác định rõ trong design document:
  - PostgreSQL là **source of truth** cho custom alert runtime
  - Iceberg là **analytics destination**
- Gắn nhãn rõ trong sơ đồ kiến trúc: `OLTP Layer`, `OLAP Layer`, `Bridge Layer`

### Phase 2 — Viết tài liệu bridge contract

Tạo file:

```text
/docs/oltp-olap-bridge.md
```

Nội dung gồm:

- Mục tiêu của bridge
- Source table / target table
- Column mapping
- Sync policy (incremental by watermark)
- Latency expectation
- Ownership và one-way data flow

### Phase 3 — Chuẩn hóa Spark sync job

Cập nhật job `sync_custom_alerts` để:

- Đọc watermark từ PostgreSQL
- Query dữ liệu incremental
- Map fields theo đúng contract
- Ghi append vào `gold.fact_alert_history`
- Update watermark sau khi ghi thành công

### Phase 4 — Đồng bộ với tài liệu chính của project

Cập nhật các phần sau trong plan tổng thể:

- **Data model section**: ghi rõ `user_alert_events` là OLTP event table
- **Architecture section**: thêm bridge layer PostgreSQL → Spark → Iceberg
- **Analytics section**: ghi rõ custom alerts được đưa vào Lakehouse chỉ để phục vụ historical analysis và dashboard

---

## 5. Kết luận

Hai cải tiến này không làm thay đổi kiến trúc tổng thể, nhưng giúp thiết kế trở nên chặt chẽ hơn rất nhiều:

- **Cải tiến 1** giúp việc sync OLTP → Lakehouse an toàn và ổn định hơn bằng watermark incremental sync
- **Cải tiến 2** giúp lớp tích hợp OLTP–OLAP có contract rõ ràng, đúng chuẩn thiết kế hệ thống dữ liệu hiện đại

Nhờ đó, kiến trúc của project vừa giữ được tính khả thi trong scope đồ án, vừa thể hiện được tư duy thiết kế data platform bài bản: tách đúng vai trò giữa transactional data và analytical data, đồng thời kiểm soát tốt lớp bridge ở giữa.
