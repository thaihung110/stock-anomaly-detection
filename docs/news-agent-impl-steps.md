# LLM News Agent — Implementation Steps (theo thứ tự)

> Plan triển khai chi tiết cho **LLM news-validation agent** (Layer 1). Bám theo file này khi code.
> Thiết kế đầy đủ: [ai-agent-plan.md](./ai-agent-plan.md). Digest recap (Phase 6) **hoãn**, làm sau.
>
> **Nguyên tắc xuyên suốt:** hệ thống đang chạy KHÔNG được gãy ở bất kỳ bước nào. alert-service giữ
> `DELIVERY_SOURCE=raw` đến tận bước cuối; "bật AI" là thao tác cuối cùng và **rollback tức thì**.

---

## 0. Tham chiếu Catalog / Namespace (BẮT BUỘC đọc trước)

3 layer = **3 catalog Gravitino tách biệt**. Sai catalog/namespace → đọc bảng rỗng → mọi alert thành UNEXPLAINED.

| Layer  | Gravitino catalog | Namespace    | Spark catalog name | PyIceberg `warehouse` | Bảng dùng trong agent                               |
| ------ | ----------------- | ------------ | ------------------ | --------------------- | --------------------------------------------------- |
| bronze | `bronze`          | `raw`        | `gravitino_bronze` | `bronze`              | `raw.raw_news_articles` (tin đuôi tươi)             |
| silver | `silver`          | `normalized` | `gravitino_silver` | `silver`              | `normalized.news_clean` (tin lịch sử/digest)        |
| gold   | `gold`            | `gold`       | `gravitino_gold`   | `gold`                | `gold.fact_alert_history`, `gold.anomaly_judgement` |

**Identifier đầy đủ (đã verify từ config):**

- Tin đuôi tươi: `raw.raw_news_articles` trong catalog `bronze` — [news-ingest AppConfig.scala:36](../spark-application/news-ingest-stream/src/main/scala/com/stockanomalydetection/newsingest/config/AppConfig.scala#L36) ghi `gravitino_bronze.raw.raw_news_articles`.
- Tin lịch sử: `normalized.news_clean` trong catalog `silver` — [news-cleaner AppConfig.scala:27](../spark-application/news-cleaner/src/main/scala/com/stockanomalydetection/newscleaner/config/AppConfig.scala#L27) ghi `gravitino_silver.normalized.news_clean`.
- Gold: rule-engine dùng `warehouse="gold"`, table `gold.<name>` — [rule-engine config.py:20](../services/rule-engine/src/rule_engine/config.py#L20).

> ⚠️ **llm-agent phải load 2 catalog** (`bronze` + `silver`) để union tin — khác rule-engine chỉ load `gold`.
> ⚠️ Catalog URI/realm từng lệch giữa service (data-lineage Finding F5) — **verify lại env deploy thật**, đừng tin mỗi default.

---

## STAGE A — Contracts & chuẩn bị alert-service (chưa có llm-agent, không đổi hành vi)

### Bước 1 — Contracts 2 phía

- **Làm:** `ConfirmedAlertEvent` (superset `AlertEvent`: + `llm_judgement`, `final_explanation`, `news_summary`, `news_category`, `news_refs[]`, `agent_version`) + `FollowUpEvent` (`ref_alert_id`, `prev_judgement`, `new_judgement`, `news_summary`, `news_refs`, `emitted_at`).
- **Ở đâu:** `llm-agent/schema.py` **và** mirror [alert-service/schema.py](../services/alert-service/src/alert_service/schema.py).
- **Quan trọng:** KHÔNG đổi `AlertEvent` (giữ `alert_id`, không rename). `ConfirmedAlertEvent` kế thừa → alert-service đọc được cả message cũ.
- **Verify:** unit test round-trip serialize/deserialize cả 2 message.

### Bước 2 — alert-service sẵn sàng nhận (vẫn chạy raw mode)

- **Làm:**
  - Flag `DELIVERY_SOURCE` (`raw` | `confirmed`), **default `raw`**.
  - `formatter.py`: thêm khối "🤖 AI Analysis" (§6.1–6.5 plan), `parse_mode=None`.
  - Handler `FollowUpEvent` (thread theo `ref_alert_id`).
  - `WATCHLIST_GATING` cho MEDIUM explained.
- **Verify:** test formatter từng dạng (EXPLAINED / UNEXPLAINED / UNCERTAIN / follow-up) + backward-compat với `AlertEvent` cũ.
- **Deploy được:** ✅ vẫn raw mode → người dùng không thấy gì đổi.

---

## STAGE B — Dựng llm-agent (publish ra `alerts.confirmed` nhưng CHƯA ai consume)

### Bước 3 — Skeleton service

- **Làm:** `services/llm-agent/` + `pyproject.toml`; `config.py` (pydantic-settings: topics, LLM model, TTL, news window, recheck, **2 catalog bronze+silver**), `metrics.py`, `main.py` (FastStream consumer `alerts.raw`, `/health`, `/metrics`).
- **Verify:** service chạy, consume được `alerts.raw`, log mỗi message (chưa xử lý).

### Bước 4 — News retrieval (union 2 catalog)

- **Làm:** `infrastructure/news_reader.py` — PyIceberg đọc **union**:
  - catalog `bronze` → `raw.raw_news_articles` (đuôi tươi, `published_at >= now - NEWS_LOOKBACK_HOURS`)
  - catalog `silver` → `normalized.news_clean` (lịch sử, `published_at >= now - NEWS_LOOKBACK_DAYS`)
  - → dedup `md5(title)` / url → top-K theo `published_at DESC`.
  - Pattern catalog/auth theo [context_loader.py](../services/rule-engine/src/rule_engine/infrastructure/context_loader.py), nhưng **load 2 catalog**.
- **Kiểm tra kỹ:** catalog `bronze` + namespace `raw` (KHÔNG `gravitino_catalog`, KHÔNG `bronze.raw_news_articles` thiếu namespace). Xem §0.
- **Verify:** với 1 symbol thật, in danh sách tin lấy được từ cả 2 nguồn; kiểm tra dedup.

### Bước 5 — LLM abstraction (provider-agnostic)

- **Làm:** `llm/base.py` (`ClassifyResult` Pydantic — output schema chung), `llm/factory.py` (`init_chat_model(cfg.llm_model).with_structured_output(ClassifyResult)`), `llm/prompts.py` (classify + relevance gate).
- **Verify:** chạy với Gemini; đổi `LLM_MODEL` sang provider khác chạy lại được — không sửa code.

### Bước 6 — LangGraph core + publish

- **Làm:** `graph/state.py`, `graph/nodes.py` (ingest → retrieve_news → classify → route — **không persist**), `graph/build.py`; `infrastructure/publisher.py`.
  - route: EXPLAINED / UNEXPLAINED → publish `ConfirmedAlertEvent`; UNEXPLAINED còn `schedule_recheck` (Bước 8).
- **Verify (end-to-end thủ công):** publish 1 `AlertEvent` test vào `alerts.raw` → quan sát `ConfirmedAlertEvent` đúng ra `alerts.confirmed`. **Chưa ai consume → an toàn tuyệt đối.**

---

## STAGE C — An toàn & follow-up

### Bước 7 — Decoupling safety

- **Làm:** TTL fail-open (`AGENT_TTL_SEC` → UNCERTAIN), circuit breaker khi LLM lỗi liên tục, idempotency theo `alert_id` (`DEDUP_CACHE_TTL_SEC`), cache `(symbol, news_hash)`.
- **Verify:** giả lập LLM chậm/chết → message vẫn ra `alerts.confirmed` với `UNCERTAIN`, không treo.

### Bước 8 — Follow-up re-check

- **Làm:** `infrastructure/recheck_queue.py` (bounded, đúng 1 cửa sổ `RECHECK_DELAY_MIN`); node `schedule_recheck`; chỉ emit `FollowUpEvent` khi **LẬT** hoặc **XÁC NHẬN** (§5.3 plan).
- **Verify:** 3 nhánh — tin ra sau → FollowUpEvent EXPLAINED; hết cửa sổ không tin → FollowUpEvent UNEXPLAINED confirmed; "vẫn chưa có gì" → KHÔNG gửi.

---

## STAGE D — (Opt-in) Persistence analytics — _có thể hoãn nếu chỉ cần luồng giao alert_

### Bước 9 — `anomaly_judgement` (append-only, ở alert-service)

- **Làm:** ở **alert-service** (load catalog `gold`): `judgement_writer.py` (PyIceberg `append()`), ensure-create `gold.anomaly_judgement` khi `DELIVERY_SOURCE=confirmed`.
  - `handle_alert(ConfirmedAlertEvent)` → append row `revision=0`.
  - `handle_followup(FollowUpEvent)` → append row `revision=N+1`, `is_flip` nếu lật. KHÔNG sửa row cũ.
  - Schema + query: §11.5 plan.
- **Verify:** sau 1 alert + 1 follow-up, bảng có đúng 2 row cùng `alert_id`.

---

## STAGE E — Test & cutover

### Bước 10 — Tests ≥80% + eval

- Unit: routing, fail-open, relevance gate, follow-up flip/confirm, dedup (mock LLM).
- Contract test: `ConfirmedAlertEvent` / `FollowUpEvent` ↔ alert-service.
- Provider test: ≥2 provider qua đổi `LLM_MODEL`.
- Eval: golden set → accuracy EXPLAINED/UNEXPLAINED + hallucination rate (refs rỗng nhưng EXPLAINED).

### Bước 11 — Deploy & BẬT AI (thao tác cuối, reversible)

1. Tạo topic `alerts.confirmed`.
2. k8s deploy `llm-agent` + secret API keys. Chạy "shadow": đã publish `alerts.confirmed` nhưng alert-service vẫn raw.
3. Quan sát `alerts.confirmed` vài giờ: judgement hợp lý? latency ổn? fail-open chạy?
4. **Flip:** alert-service `DELIVERY_SOURCE=confirmed` → user nhận alert kèm khối AI.
5. **Rollback bất kỳ lúc:** đổi lại `DELIVERY_SOURCE=raw` → về luồng cũ tức thì.

---

## Critical path

```
1 contracts → 2 alert-service(raw) → 3 skeleton → 4 news(2 catalog) → 5 llm → 6 graph+publish
   → 7 safety → 8 follow-up → 10 tests → 11 cutover
        (9 anomaly_judgement: nhánh phụ, song song hoặc sau)
```

## 3 điểm dễ vấp

1. **Catalog/namespace (Bước 4):** `bronze`+`raw` / `silver`+`normalized`, load 2 catalog. Sai → mọi alert UNEXPLAINED. Xem §0.
2. **Thứ tự deploy:** Bước 2 (alert-service biết đọc `ConfirmedAlertEvent`) phải xong & deploy **trước** khi flip ở Bước 11.
3. **Tách bạch test:** Bước 6 publish thủ công vào `alerts.raw` để verify "agent đúng" **trước** khi đụng cutover ("delivery đúng").
