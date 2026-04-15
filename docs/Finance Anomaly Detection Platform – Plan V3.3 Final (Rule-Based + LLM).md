# Finance Anomaly Detection Platform – Plan V3.3 Final

## Rule-Based Detection + LLM Validation | Star Schema Data Modeling

> **Changelog từ V3.3 Rev2:**
>
> - Thêm mục 0: Problem Description, User Value & Use Case Scenarios (lấy cảm hứng từ UnusualVolumeDetector)
> - Thêm mục 2.4: Alpha Vantage — nguồn dữ liệu bổ sung với rate limit & volume estimate
> - Thêm Bảng tổng hợp Data Volume Estimate cho tất cả nguồn
> - Thay thế toàn bộ Silver & Gold schema thành Star Schema (Fact + Dimension)

---

## 0. Problem Description & User Value

### 0.1 Bài toán

Hàng ngày, thị trường chứng khoán Mỹ giao dịch hơn **11 tỷ cổ phiếu** với hàng triệu sự kiện bất thường tiềm ẩn. Nhà đầu tư cá nhân và quản lý rủi ro **không thể theo dõi thủ công** hàng trăm mã cổ phiếu để phát hiện kịp thời các biến động giá/volume bất thường.[^1][^2]

Các công cụ hiện có (Bloomberg, Finviz unusual volume screener) hoặc rất tốn kém hoặc không cung cấp ngữ cảnh để giải thích tại sao biến động đó xảy ra. Nhà đầu tư nhận được cảnh báo "volume spike" nhưng không biết nguyên nhân là do tin tức thật hay dữ liệu lỗi.[^3][^1]

**Hệ thống này giải quyết:**

1. Tự động scan thị trường real-time để phát hiện volume/price anomalies.
2. Dùng LLM Agent để tìm tin tức và phân loại anomaly: có tin giải thích hay không.
3. Push Telegram alert với đầy đủ ngữ cảnh để nhà đầu tư ra quyết định nhanh.

**Inspiration:** UnusualVolumeDetector (GitHub, 975 stars, 268 forks) — script Python scan toàn thị trường, lấy 5 tháng lịch sử volume, alert khi volume vượt 10 standard deviations trong 3 ngày gần nhất. Hệ thống này là phiên bản **production-grade** với Big Data stack, LLM integration, và real-time streaming.[^2]

---

### 0.2 Target Users

| User                                | Mô tả                                   | Nhu cầu chính                                                         |
| ----------------------------------- | --------------------------------------- | --------------------------------------------------------------------- |
| **Swing Trader / Technical Trader** | Giao dịch ngắn hạn, tìm cơ hội breakout | Phát hiện unusual volume sớm, trước khi tin tức lan rộng              |
| **Long-term Investor**              | Nắm giữ danh mục dài hạn                | Cảnh báo rủi ro khi cổ phiếu trong danh mục có biến động bất thường   |
| **Risk Manager**                    | Quản lý rủi ro danh mục                 | Giám sát portfolio tránh loss do biến động mạnh không giải thích được |
| **Market Analyst**                  | Nghiên cứu hành vi thị trường           | Phân tích patterns anomaly theo loại news, sector, thời điểm          |

---

### 0.3 User Value — Insight nhà đầu tư nhận được

#### Value 1: Radar Cơ Hội — Unusual Volume Scanner

**Vấn đề:** Nhiều breakout lớn được báo trước bởi volume spike bất thường (3–10x bình quân) trước khi tin tức chính thức được công bố rộng rãi.[^1][^3]

**Insight hệ thống cung cấp:**

- "Top 10 mã hôm nay có volume gấp ≥3x bình quân 20 ngày."
- "NVDA: volume gấp 4.2x, giá tăng +3.1%, không tìm thấy tin tức — đáng chú ý."

**Cách dùng:** Trader nhận alert buổi sáng, đưa các mã `UNEXPLAINED` vào watchlist để theo dõi tiếp.

#### Value 2: Noise Filter — Explained vs Unexplained Anomaly

**Vấn đề:** Trong một ngày thị trường biến động, có hàng chục mã có spike. 80% là do tin tức đã được biết (earnings release, rate announcement). Trader mất thời gian check thủ công từng mã.[^1]

**Insight hệ thống cung cấp:**

- Tag `NEWS_EXPLAINED`: "Biến động do earnings miss — thị trường đang phản ứng bình thường với thông tin đã công khai. Không phải alpha."
- Tag `UNEXPLAINED`: "Không tìm thấy tin tức. Volume/price đang lạ — có thể là dòng tiền tổ chức, tin đồn chưa public, hoặc wash trading."
- Tag `DATA_ERROR`: "Cross-check Finnhub vs yfinance phát hiện sai lệch >10% — dữ liệu có vấn đề, không ra quyết định."

**Cách dùng:** User chỉ cần tập trung vào top 3–5 alert `UNEXPLAINED HIGH severity` mỗi ngày, tiết kiệm nhiều giờ research.

#### Value 3: Portfolio Early Warning — Risk Monitoring

**Vấn đề:** Long-term investor không theo dõi chart suốt ngày. Khi cổ phiếu bị "đập" mạnh, họ phát hiện quá trễ hoặc panic sell mà không hiểu nguyên nhân.[^4]

**Insight hệ thống cung cấp:**

- "AAPL trong danh mục của bạn vừa có PRICE_DROP (−7.2%, z-score: −4.1σ). AI tìm thấy tin: earnings miss Q2. Đây là phản ứng với fundamental, không phải glitch."
- "JPM trong danh mục của bạn có VOLUME_SPIKE (4.5x). Không tìm thấy tin tức — theo dõi thêm."

**Cách dùng:** User khai báo danh mục → hệ thống luôn ưu tiên alert cho portfolio symbols với label rõ ràng.

#### Value 4: Market Behavior Analytics

**Vấn đề:** Analyst và researcher muốn hiểu "loại news nào thường gây ra anomaly mạnh nhất" để thiết kế chiến lược event-driven.[^5][^6]

**Insight hệ thống cung cấp:**

- Dashboard "Anomaly × News Category": biểu đồ heatmap (thời gian vs news_category vs severity).
- "Trong 3 tháng qua, anomalies do earnings chiếm 42%, do macro chiếm 31%, unexplained 27%."
- "NVDA có tỷ lệ UNEXPLAINED cao nhất (40%) trong nhóm Semiconductor."

**Cách dùng:** Analyst dùng để hiểu sector behavior, thiết kế chiến lược phù hợp.

---

### 0.4 User Scenarios

#### Scenario A — Swing Trader tìm breakout

> **User:** Trader chuyên giao dịch swing, theo dõi ~150 mã Nasdaq
>
> **08:00 AM** (trước mở cửa): Vào dashboard "Unusual Volume Today" → thấy top 20 mã volume >3x avg đêm qua (pre-market).
>
> **10:23 AM**: Nhận Telegram: `🔴 NVDA — VOLUME_SPIKE — UNEXPLAINED — HIGH` → volume 4.2x, giá +3.1%, không có tin tức lớn.
>
> **Hành động:** Trader đưa NVDA vào watchlist đặc biệt, đặt alert giá tại breakout level, chờ xác nhận tiếp.
>
> **Giá trị:** Phát hiện sớm trước khi tin tức (nếu có) lan rộng. Không cần manually scan 150 mã.

#### Scenario B — Long-term Investor nhận early warning

> **User:** Investor nắm giữ: AAPL, MSFT, NVDA, JPM, BRK-B
>
> **14:37 PM**: NVDA bị dump −8.5% với volume spike 5.1σ.
>
> **14:38 PM**: Nhận Telegram: `🔴 NVDA — PRICE_DROP + VOLUME_SPIKE — HIGH` → LLM tìm thấy: "Morgan Stanley downgrade + analyst price target cut."
> Tag: `NEWS_EXPLAINED`.
>
> **Hành động:** Investor bình tĩnh — đây là fundamental news, không phải glitch hay panic sell vô lý. Quyết định giữ hoặc average down.
>
> **Giá trị:** Không panic sell vì hiểu nguyên nhân. Alert có context chứ không chỉ là con số.

#### Scenario C — Risk Manager giám sát danh mục

> **User:** Risk manager quản lý fund với 30 positions
>
> **09:45 AM**: Nhận alert: `🟡 GME — VOLUME_SPIKE — UNEXPLAINED — MEDIUM` → volume 8.9x avg, giá +12%.
>
> **Hành động:** Risk manager check thêm open positions liên quan GME, xem xét hedge nếu cần. Không mất thời gian scan từng mã.
>
> **Giá trị:** Phát hiện market anomaly ngay khi xảy ra, đủ thời gian để response.

#### Scenario D — Analyst nghiên cứu event impact

> **User:** Analyst nghiên cứu "earnings announcement effect on price/volume"
>
> **Cuối quý:** Vào Superset dashboard "Anomaly History Explorer" → filter: anomaly_type = PRICE_SPIKE, news_category = earnings, period = Q1 2026.
>
> **Truy vấn Trino:**
>
> ```sql
> SELECT symbol, avg(abs(daily_return)) as avg_move,
>        count(*) as anomaly_count
> FROM gold.fact_anomaly_daily
> JOIN gold.dim_news_category ON news_category_key = dim_news_category.category_key
> WHERE news_category = 'earnings' AND severity = 'HIGH'
> GROUP BY symbol ORDER BY avg_move DESC;
> ```
>
> **Kết quả:** "Top 5 mã có earnings impact lớn nhất Q1 2026."
>
> **Giá trị:** Dùng để thiết kế earnings surprise strategy.

---

## 1. Phạm vi & Phương pháp

**Phương pháp phát hiện (2 layer):**

- **Layer 0: Rule-Based Detection** — Z-Score + Threshold rules, real-time, CPU only
- **Layer 1: LLM Multi-Agent Validation** — LangGraph + Gemini 2.5 Flash-Lite

**Nguồn tham khảo thiết kế:**

- Park (2024) — LLM Multi-Agent Framework for Financial Anomaly Detection[^7][^8]
- andrewm4894/anomaly-agent — LangGraph 2-stage pipeline[^9]
- SamPom100/UnusualVolumeDetector — Simple but effective Z-score volume scanner[^2]
- LangGraph conditional edges & multi-agent routing[^10][^11]

---

## 2. Data Sources & Bronze Schema

### 2.0 Nguyên tắc thiết kế Bronze Layer

Để tránh overlap và giữ stack gọn, Bronze layer tuân thủ các quy tắc sau:

1. **Mỗi loại dữ liệu chỉ có 1 nguồn chính** — không có nguồn song song cho cùng loại.
2. **OHLCV daily**: yfinance làm main feed duy nhất, incremental daily → Iceberg.
3. **Real-time quotes**: không lưu vào DB; tồn tại **thuần túy trong Kafka** topic `raw.stock.quotes` → Rule Engine consume trực tiếp.
4. **Intraday tick**: Finnhub WS → Kafka `raw.stock.trades` → Spark Structured Streaming aggregate → `silver.ohlcv_1min` (Iceberg). Không lưu raw tick vào bất kỳ DB nào.
5. **News**: **chỉ dùng NewsAPI** làm nguồn duy nhất (loại bỏ Finnhub company news). Poll định kỳ, đẩy vào Kafka `raw.stock.news`, Spark ghi vào Iceberg `bronze.raw_news_articles`.
6. **Company metadata**: yfinance `.info` batch weekly → Iceberg `bronze.raw_company_info` → Gold `dim_symbol`.
7. **Không dùng TimescaleDB** — toàn bộ lưu trữ dựa trên Kafka (streaming) và Iceberg/MinIO (batch + analytics).

```
Bronze Source Map (2 nguồn, không overlap):

  daily OHLCV    →  yfinance batch         →  bronze.raw_ohlcv_daily   [Iceberg, incremental daily]
  company meta   →  yfinance .info batch   →  bronze.raw_company_info  [Iceberg, weekly]
  real-time quote→  yfinance WebSocket     →  Kafka: raw.stock.quotes  [Kafka-only, no DB]
  trade tick     →  Finnhub WebSocket      →  Kafka: raw.stock.trades  [Kafka-only, no DB]
  news           →  NewsAPI REST (5min)    →  bronze.raw_news_articles [Iceberg, via Spark]
```

---

### 2.1 Nguồn 1 — Yahoo Finance (yfinance) — MAIN OHLCV + Quotes + Metadata

**API/Library:** `yfinance` Python v0.2.x+[^12][^13]

| Endpoint                                   | Method       | Mô tả                                                    | Vai trò                   |
| ------------------------------------------ | ------------ | -------------------------------------------------------- | ------------------------- |
| `yf.download(tickers, period, interval)`   | Batch REST   | OHLCV daily 20+ năm                                      | **Main daily OHLCV**      |
| `yf.AsyncWebSocket().subscribe([symbols])` | Streaming WS | Real-time level-1 quotes                                 | **Main real-time feed**   |
| `yf.Ticker(symbol).fast_info` / `.info`    | Batch REST   | Company metadata (sector, exchange, market cap, beta...) | **Main company metadata** |

**Rate limit:** Không có rate limit chính thức[^14]

**Volume ước tính:**

- 500 symbols × 20 năm × 252 ngày/năm = ~2.52 triệu rows daily OHLCV ≈ **252 MB**
- Company metadata 500 symbols ≈ **5 MB** (JSON)
- Intraday: **không lưu vào Bronze** (dùng Finnhub trades làm nguồn intraday duy nhất)

**Schema: `bronze.raw_ohlcv_daily`** — Main OHLCV feed, incremental daily, Iceberg

```sql
CREATE TABLE bronze.raw_ohlcv_daily (
    symbol          VARCHAR(20)   NOT NULL,
    trade_date      DATE          NOT NULL,
    open            DOUBLE        NOT NULL,
    high            DOUBLE        NOT NULL,
    low             DOUBLE        NOT NULL,
    close           DOUBLE        NOT NULL,
    adj_close       DOUBLE,
    volume          BIGINT        NOT NULL,
    dividends       DOUBLE,
    stock_splits    DOUBLE,
    source          VARCHAR(20)   DEFAULT 'yfinance',
    ingested_at     TIMESTAMP     DEFAULT NOW(),
    PRIMARY KEY (symbol, trade_date)
) USING iceberg PARTITIONED BY (months(trade_date));
```

**Schema: `bronze.raw_company_info`** — Company metadata từ yfinance.info, Iceberg

```sql
CREATE TABLE bronze.raw_company_info (
    symbol              VARCHAR(20)   PRIMARY KEY,
    short_name          VARCHAR(200),
    long_name           VARCHAR(200),
    exchange            VARCHAR(20),         -- "NMS", "NYQ"
    quote_type          VARCHAR(20),         -- "EQUITY", "ETF"
    sector              VARCHAR(100),
    industry            VARCHAR(200),
    country             VARCHAR(50),
    currency            VARCHAR(5),
    website             TEXT,
    market_cap          BIGINT,
    beta                DOUBLE,
    trailing_pe         DOUBLE,
    forward_pe          DOUBLE,
    fifty_two_week_high DOUBLE,
    fifty_two_week_low  DOUBLE,
    fifty_day_average   DOUBLE,
    two_hundred_day_avg DOUBLE,
    shares_outstanding  BIGINT,
    dividend_yield      DOUBLE,
    fetched_at          TIMESTAMP     DEFAULT NOW()
) USING iceberg;
```

**Kafka topic `raw.stock.quotes`** — Real-time level-1 quotes (Kafka-only, không lưu DB)

Không có bảng Bronze tương ứng. Quote tồn tại trong Kafka theo retention window (mặc định 7 ngày), Rule Engine consume trực tiếp.

**Message schema (JSON):**

```json
{
  "symbol": "NVDA",
  "price": 178.52,
  "change": 5.43,
  "change_pct": 3.14,
  "day_volume": 82000000,
  "day_high": 180.1,
  "day_low": 173.2,
  "prev_close": 173.09,
  "market_state": "REGULAR",
  "exchange": "NMS",
  "event_ts": "2026-03-26T10:23:15Z"
}
```

> **Thiết kế Kafka-only cho quotes:** Rule Engine cần `price`, `day_volume`, `day_high/low`, `prev_close` — tất cả đều có trong message. Context dài hạn (rolling 20d) được lấy từ `gold.rule_engine_context` (Iceberg) được pre-load vào bộ nhớ khi service khởi động. Không cần DB trung gian.

---

### 2.2 Nguồn 2 — Finnhub — MAIN Tick Data (chỉ WebSocket trades)

**API:** WebSocket `wss://ws.finnhub.io?token=<API_KEY>`[^15][^16]

> **Lưu ý:** Finnhub chỉ còn được dùng cho **WebSocket trade tick**. Finnhub Company News đã bị loại — nguồn news duy nhất là NewsAPI.

**Rate limit (free):** 60 API calls/phút, 30 WebSocket connections[^16]

**Volume ước tính:**

- Tick data: ~500 symbols × 1000 trades/ngày × 252 ngày = ~126 triệu rows/năm ≈ **2.1 GB**

**Kafka topic `raw.stock.trades`** — Trade tick data (Kafka-only, không lưu DB)

Không có bảng Bronze tương ứng. Tick data chỉ sống trong Kafka, được Spark Structured Streaming consume để aggregate → `silver.ohlcv_1min` (Iceberg).

**Message schema (JSON):**

```json
{
  "symbol": "NVDA",
  "price": 178.45,
  "volume": 250,
  "timestamp_ms": 1743034995000,
  "conditions": ["1"]
}
```

> **Thiết kế Kafka-only cho trades:** Tick raw không có giá trị lưu trữ vĩnh viễn trong đồ án; giá trị nằm ở bảng `silver.ohlcv_1min` sau khi aggregate. Spark job đọc Kafka `raw.stock.trades`, micro-batch mỗi 60s, aggregate OHLCV + VWAP theo 1-minute window, ghi Iceberg.

---

### 2.3 Nguồn 3 — NewsAPI.org — Nguồn news duy nhất

**API:** REST[^17]

**Rate limit (free):** Developer plan: 100 requests/day, last 24h only[^18]

**Tại sao chỉ giữ NewsAPI:**

- NewsAPI và Finnhub Company News đều cụng cấp tin tức liên quan đến cùng một symbol → nội dung thực tế có thể trùng nhau cao.
- NewsAPI đủ cho mục đích LLM validation (6h window). Giữ 1 nguồn giúp pipeline đơn giản, dễ debug, tránh dedup phức tạp.

**Volume ước tính:** 100 req/day × 10 articles/req = ~1000 articles/ngày ≈ **200 MB/năm**

**Schema: `bronze.raw_news_articles`** — Iceberg, partition theo ngày

```sql
CREATE TABLE bronze.raw_news_articles (
    article_id      VARCHAR(64)   NOT NULL,   -- MD5(url)
    symbol          VARCHAR(20),              -- NULL nếu general market news
    source_name     VARCHAR(100),
    author          VARCHAR(200),
    title           TEXT          NOT NULL,
    description     TEXT,
    url             TEXT          NOT NULL,
    published_at    TIMESTAMP     NOT NULL,
    content         TEXT,
    search_query    VARCHAR(50),              -- query keyword dùng khi fetch
    fetched_at      TIMESTAMP,
    PRIMARY KEY (article_id)
) USING iceberg PARTITIONED BY (days(published_at));
```

**Ingest flow:**

```
NewsAPI poller (Python, every 5min)
  └─► Kafka: raw.stock.news
        └─► Spark Structured Streaming micro-batch
              └─► bronze.raw_news_articles (Iceberg/MinIO)
```

LLM Agent (Node 2a) khi cần news cho một symbol:

```sql
-- Query Trino trên Iceberg
SELECT title, description, published_at
FROM bronze.raw_news_articles
WHERE (symbol = 'NVDA' OR search_query LIKE '%NVDA%')
  AND published_at >= NOW() - INTERVAL '6' HOUR
ORDER BY published_at DESC
LIMIT 10;
```

---

### 2.4 Tổng hợp Bronze Layer — 2 nguồn, không overlap, không TimescaleDB

| Loại dữ liệu     | Nguồn                  | Lưu trữ                                 | Tần suất        | Điến đâu?                               |
| ---------------- | ---------------------- | --------------------------------------- | --------------- | --------------------------------------- |
| Daily OHLCV      | yfinance batch         | **Iceberg**: `bronze.raw_ohlcv_daily`   | Daily 06:00 UTC | → `silver.ohlcv_daily`                  |
| Company metadata | yfinance `.info` batch | **Iceberg**: `bronze.raw_company_info`  | Weekly          | → `gold.dim_symbol`                     |
| Real-time quote  | yfinance WebSocket     | **Kafka-only**: `raw.stock.quotes`      | Real-time       | → Rule Engine (consume trực tiếp)       |
| Trade tick       | Finnhub WebSocket      | **Kafka-only**: `raw.stock.trades`      | Real-time       | → Spark aggregate → `silver.ohlcv_1min` |
| News articles    | NewsAPI REST           | **Iceberg**: `bronze.raw_news_articles` | Every 5min      | → LLM Agent + `silver.news_clean`       |

> **Không có TimescaleDB trong stack.** Quotes và trades sống trong Kafka theo retention 7 ngày. Chỉ có dữ liệu cần lưu lâu dài (daily OHLCV, metadata, news) mới đi vào Iceberg.

**Volume ước tính (500 symbols):**

| Nguồn                                                           | Volume ước tính | Tier      |
| --------------------------------------------------------------- | --------------- | --------- |
| yfinance daily OHLCV (20 năm lịch sử)                           | ~252 MB         | Free      |
| yfinance company info (500 symbols)                             | ~5 MB           | Free      |
| Finnhub tick data (Kafka, 2 năm, aggregate → silver.ohlcv_1min) | ~2.1 GB         | Free[^16] |
| NewsAPI articles (2 năm)                                        | ~200 MB         | Free[^18] |
| silver.ohlcv_1min (1m bars từ aggregate tick)                   | ~800 MB         | —         |
| gold Star Schema tables                                         | ~500 MB         | —         |
| **Tổng ước tính (Iceberg/MinIO)**                               | **~3.9 GB**     |           |

---

## 3. Data Flow Tổng Thể

```
┌────────────────────────────────────────────────────────────────────┐
│                      EXTERNAL DATA SOURCES                          │
│  yfinance WS  │  Finnhub WS  │  yfinance batch  │  Alpha Vantage  │
│               │              │  Finnhub REST    │  NewsAPI        │
└────────────────────────────────┬───────────────────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│                      REDPANDA TOPICS                                 │
│  raw.stock.quotes   ← yfinance WebSocket producer                  │
│  raw.stock.trades   ← Finnhub WebSocket producer                   │
│  raw.stock.news     ← NewsAPI + Finnhub News polling (5min)        │
│  alerts.raw         ← Rule Engine output                           │
│  alerts.confirmed   ← LLM Agent output                             │
└───────────┬────────────────────────────────────────────────────────┘
            │
     ┌──────┴───────────────────────────────────────┐
     │ (FastStream consumers, real-time)             │ (Spark batch, daily)
     ▼                                               ▼
┌──────────────────────┐              ┌──────────────────────────────┐
│  BRONZE (TimescaleDB)│              │  BRONZE (Iceberg/MinIO)      │
│  raw_quotes_stream   │              │  raw_ohlcv_daily (yfinance)  │
│  raw_trades_stream   │              │  raw_ohlcv_av_daily (AV)     │
│  raw_news_articles   │              │  raw_company_overview_av     │
│  raw_company_news    │              │  raw_technical_indicators_av │
└──────────┬───────────┘              └──────────────┬───────────────┘
           │                                         │
           └──────────────────────┬──────────────────┘
                                  │ (Spark batch daily)
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│              SILVER LAYER (Iceberg/MinIO) — Star Schema Prep        │
│  silver.ohlcv_daily   — Cleaned, split-adjusted                    │
│  silver.ohlcv_1min    — Aggregated 1-min bars                      │
│  silver.news_clean    — Deduplicated, normalized news              │
└────────────────────────────────┬───────────────────────────────────┘
                                 │ (Spark batch daily)
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│              GOLD LAYER (Iceberg/MinIO) — Star Schema              │
│  DIMENSION TABLES:                                                  │
│    dim_symbol       — Company metadata (slowly changing)           │
│    dim_date         — Date calendar (pre-populated)                │
│    dim_time         — Intraday time slots                          │
│    dim_news_category — News category taxonomy                      │
│    dim_anomaly_type  — Anomaly type taxonomy                       │
│    dim_rule         — Rule engine rule definitions                 │
│  FACT TABLES:                                                       │
│    fact_ohlcv_daily    — Daily OHLCV measures (grain: symbol×day)  │
│    fact_anomaly_daily  — Anomaly events (grain: symbol×event)      │
│    fact_alert_history  — Alert delivery log                        │
│  AGGREGATE / CONTEXT:                                               │
│    gold.rule_engine_context — Rolling stats for rule engine        │
└────────────────────────────────┬───────────────────────────────────┘
                                 │
                     ┌───────────┴───────────┐
                     ▼                       ▼
           [Rule Engine]            [Trino Analytics]
           [LLM Agent]              [Grafana / Superset]
                     │
                     ▼
              [Telegram Alert]
```

---

## 4. Data Modeling — Star Schema (Silver & Gold Layer)

### 4.1 Thiết kế tổng thể Star Schema

```
                    ┌─────────────────┐
                    │   dim_date      │
                    │  (calendar dim) │
                    └────────┬────────┘
                             │
  ┌──────────────┐    ┌──────┴────────────────┐    ┌──────────────────┐
  │  dim_symbol  ├────┤  fact_ohlcv_daily     ├────┤  dim_anomaly_type│
  │  (company)   │    │  (central fact table) │    │  (type lookup)   │
  └──────────────┘    └──────┬────────────────┘    └──────────────────┘
                             │
                    ┌────────┴────────────────┐
                    │  fact_anomaly_daily     │
                    │  (anomaly events)       │
                    └────────┬────────────────┘
                             │ (FK)
            ┌────────────────┼──────────────────┐
            ▼                ▼                  ▼
    ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐
    │  dim_rule    │  │ dim_news_cat │  │ fact_alert_hist │
    │  (rules def) │  │ (news type)  │  │ (alert delivery)│
    └──────────────┘  └──────────────┘  └─────────────────┘
```

---

### 4.2 SILVER LAYER — Cleaned, Normalized Tables

**Silver layer** là bước làm sạch dữ liệu từ Bronze. Không dùng Star Schema ở Silver vì vẫn còn là "denormalized" source. Star Schema áp dụng ở Gold layer.

#### `silver.ohlcv_daily`

```sql
CREATE TABLE silver.ohlcv_daily (
    symbol          VARCHAR(20)   NOT NULL,
    trade_date      DATE          NOT NULL,
    open            DOUBLE        NOT NULL,
    high            DOUBLE        NOT NULL,
    low             DOUBLE        NOT NULL,
    close           DOUBLE        NOT NULL,
    adj_close       DOUBLE,                   -- fully adjusted (split + dividend)
    volume          BIGINT        NOT NULL,
    vwap_estimate   DOUBLE,                   -- (open+high+low+close)/4 * volume estimate
    is_complete     BOOLEAN       DEFAULT TRUE,
    data_source     VARCHAR(20),              -- "yfinance", "alpha_vantage"
    updated_at      TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
) USING iceberg PARTITIONED BY (months(trade_date));
```

#### `silver.ohlcv_1min`

```sql
CREATE TABLE silver.ohlcv_1min (
    symbol          VARCHAR(20)   NOT NULL,
    bar_ts          TIMESTAMP     NOT NULL,
    open            DOUBLE        NOT NULL,
    high            DOUBLE        NOT NULL,
    low             DOUBLE        NOT NULL,
    close           DOUBLE        NOT NULL,
    volume          BIGINT        NOT NULL,
    trade_count     INTEGER,
    vwap            DOUBLE,
    PRIMARY KEY (symbol, bar_ts)
) USING iceberg PARTITIONED BY (days(bar_ts));
```

#### `silver.news_clean`

```sql
CREATE TABLE silver.news_clean (
    article_id      VARCHAR(64)   PRIMARY KEY,
    symbol          VARCHAR(20),
    headline        TEXT          NOT NULL,
    summary         TEXT,
    published_at    TIMESTAMP     NOT NULL,
    source_name     VARCHAR(100),
    url             TEXT,
    data_source     VARCHAR(20),              -- "newsapi", "finnhub"
    dedup_hash      VARCHAR(64)               -- hash(headline) for deduplication
) USING iceberg PARTITIONED BY (days(published_at));
```

---

### 4.3 GOLD LAYER — Star Schema (Dimension Tables)

#### `gold.dim_symbol` — Company Dimension (Slowly Changing Dimension Type 2)

```sql
CREATE TABLE gold.dim_symbol (
    symbol_key          INTEGER       PRIMARY KEY,   -- Surrogate key
    symbol              VARCHAR(20)   NOT NULL,      -- Natural key (ticker)
    company_name        VARCHAR(200),
    exchange            VARCHAR(20),                 -- "NASDAQ", "NYSE"
    sector              VARCHAR(100),                -- "Technology", "Financials"
    industry            VARCHAR(200),                -- "Semiconductors", "Banks"
    country             VARCHAR(50),                 -- "USA"
    currency            VARCHAR(5),                  -- "USD"
    market_cap          BIGINT,                      -- Latest market cap
    shares_outstanding  BIGINT,
    beta                DOUBLE,                      -- Market beta (volatility vs index)
    week_52_high        DOUBLE,
    week_52_low         DOUBLE,
    -- SCD2 tracking
    is_active           BOOLEAN       DEFAULT TRUE,
    effective_from      DATE          NOT NULL,
    effective_to        DATE,                        -- NULL = current record
    -- Source
    source              VARCHAR(20)   DEFAULT 'alpha_vantage'
) USING iceberg;
```

#### `gold.dim_date` — Date Dimension (Pre-populated calendar)

```sql
CREATE TABLE gold.dim_date (
    date_key            INTEGER       PRIMARY KEY,   -- YYYYMMDD format e.g. 20260325
    full_date           DATE          NOT NULL,
    day_of_week         INTEGER,                     -- 1=Monday … 7=Sunday
    day_name            VARCHAR(10),                 -- "Monday"
    day_of_month        INTEGER,
    day_of_year         INTEGER,
    week_of_year        INTEGER,
    month_number        INTEGER,
    month_name          VARCHAR(10),
    quarter             INTEGER,                     -- 1..4
    year                INTEGER,
    is_weekend          BOOLEAN,
    is_us_market_holiday BOOLEAN,
    is_trading_day      BOOLEAN,                     -- TRUE = NYSE open
    trading_day_number  INTEGER                      -- sequential trading day number in year
) USING iceberg;
```

#### `gold.dim_time` — Time Dimension (Intraday slot)

```sql
CREATE TABLE gold.dim_time (
    time_key            INTEGER       PRIMARY KEY,   -- HHMM format e.g. 0930
    hour                INTEGER,
    minute              INTEGER,
    time_label          VARCHAR(8),                  -- "09:30 AM"
    market_session      VARCHAR(20),                 -- "PRE", "REGULAR", "POST", "CLOSED"
    session_minute      INTEGER,                     -- Minutes from session open (0=09:30)
    is_opening_hour     BOOLEAN,                     -- 09:30–10:30
    is_closing_hour     BOOLEAN                      -- 15:00–16:00
) USING iceberg;
```

#### `gold.dim_anomaly_type` — Anomaly Type Taxonomy

```sql
CREATE TABLE gold.dim_anomaly_type (
    anomaly_type_key    INTEGER       PRIMARY KEY,
    anomaly_type        VARCHAR(30)   NOT NULL,      -- "PRICE_SPIKE", "PRICE_DROP"
    anomaly_category    VARCHAR(20),                 -- "PRICE", "VOLUME", "VOLATILITY"
    description         TEXT,
    typical_cause       TEXT,                        -- Short description of what causes this type
    risk_level          VARCHAR(10)                  -- "LOW", "MEDIUM", "HIGH"
) USING iceberg;

-- Pre-populated values:
-- (1, 'PRICE_SPIKE',       'PRICE',      'Extreme positive return',     ..., 'HIGH')
-- (2, 'PRICE_DROP',        'PRICE',      'Extreme negative return',     ..., 'HIGH')
-- (3, 'VOLUME_SPIKE',      'VOLUME',     'Abnormal trading volume',     ..., 'MEDIUM')
-- (4, 'VOLATILITY_SHIFT',  'VOLATILITY', 'Extreme intraday range',      ..., 'MEDIUM')
-- (5, 'BB_BREAKOUT',       'PRICE',      'Price outside Bollinger Band',..., 'MEDIUM')
-- (6, 'RSI_EXTREME',       'MOMENTUM',   'RSI overbought/oversold',     ..., 'LOW')
```

#### `gold.dim_rule` — Rule Definitions

```sql
CREATE TABLE gold.dim_rule (
    rule_key            INTEGER       PRIMARY KEY,
    rule_code           VARCHAR(30)   NOT NULL,      -- "PRICE_Z", "VOLUME_Z"
    rule_name           VARCHAR(100),
    formula_description TEXT,                        -- Human-readable formula
    threshold_default   DOUBLE,                      -- Default threshold value
    severity_if_alone   VARCHAR(10)                  -- Severity when triggered alone
) USING iceberg;

-- Pre-populated:
-- (1, 'PRICE_Z',          'Price Z-Score',    '|return_z| > 3.0',       3.0,  'MEDIUM')
-- (2, 'VOLUME_Z',         'Volume Z-Score',   'volume_z > 3.0',         3.0,  'MEDIUM')
-- (3, 'VOLUME_RATIO',     'Volume Ratio',     'vol / avg_vol_20d > 3.5', 3.5, 'MEDIUM')
-- (4, 'BB_BREAKOUT',      'Bollinger Breakout','bb_pos > 1.0 or < 0.0', 1.0,  'MEDIUM')
-- (5, 'RSI_EXTREME',      'RSI Extreme',      'RSI > 80 or < 20',       80.0, 'LOW')
-- (6, 'INTRADAY_RANGE',   'Intraday Range',   '(high-low)/low > 0.05',  0.05, 'MEDIUM')
```

#### `gold.dim_news_category` — News Category Taxonomy

```sql
CREATE TABLE gold.dim_news_category (
    category_key        INTEGER       PRIMARY KEY,
    category_code       VARCHAR(30)   NOT NULL,      -- "earnings", "m_and_a"
    category_name       VARCHAR(100),
    description         TEXT,
    typical_price_impact VARCHAR(20)                 -- "HIGH", "MEDIUM", "LOW", "VARIES"
) USING iceberg;

-- Pre-populated:
-- (1, 'earnings',         'Earnings Release',        '...', 'HIGH')
-- (2, 'm_and_a',          'Merger & Acquisition',    '...', 'HIGH')
-- (3, 'macro',            'Macro / Fed Policy',      '...', 'HIGH')
-- (4, 'analyst_rating',   'Analyst Rating Change',   '...', 'MEDIUM')
-- (5, 'regulation',       'Regulatory News',         '...', 'VARIES')
-- (6, 'product_launch',   'Product/Service Launch',  '...', 'LOW')
-- (7, 'data_error',       'Data Feed Error',         '...', 'N/A')
-- (8, 'none',             'No News Found',           '...', 'N/A')
```

---

### 4.4 GOLD LAYER — Star Schema (Fact Tables)

#### `gold.fact_ohlcv_daily` — Central Daily OHLCV Fact Table

**Grain:** 1 row = 1 symbol × 1 trading day

```sql
CREATE TABLE gold.fact_ohlcv_daily (
    -- Surrogate Keys (FK to dimensions)
    symbol_key          INTEGER       NOT NULL,      -- FK → dim_symbol
    date_key            INTEGER       NOT NULL,      -- FK → dim_date (YYYYMMDD)

    -- Measures — Price
    open                DOUBLE        NOT NULL,
    high                DOUBLE        NOT NULL,
    low                 DOUBLE        NOT NULL,
    close               DOUBLE        NOT NULL,
    adj_close           DOUBLE,
    vwap                DOUBLE,

    -- Measures — Volume
    volume              BIGINT        NOT NULL,
    dollar_volume       DOUBLE,                      -- close × volume

    -- Measures — Derived Returns
    daily_return        DOUBLE,                      -- (close - prev_close) / prev_close
    log_return          DOUBLE,                      -- ln(close / prev_close)
    intraday_range_pct  DOUBLE,                      -- (high - low) / low
    gap_pct             DOUBLE,                      -- (open - prev_close) / prev_close

    -- Measures — Rolling Statistics (pre-computed by Spark)
    mean_return_20d     DOUBLE,
    std_return_20d      DOUBLE,
    mean_volume_20d     DOUBLE,
    std_volume_20d      DOUBLE,
    price_zscore        DOUBLE,                      -- daily_return z-score
    volume_zscore       DOUBLE,                      -- volume z-score

    -- Measures — Technical Indicators
    rsi_14              DOUBLE,
    macd_line           DOUBLE,
    macd_signal         DOUBLE,
    macd_histogram      DOUBLE,
    bb_upper            DOUBLE,
    bb_lower            DOUBLE,
    bb_mid              DOUBLE,
    bb_position         DOUBLE,                      -- (close-bb_lower)/(bb_upper-bb_lower)
    atr_14              DOUBLE,

    -- Metadata
    data_source         VARCHAR(20),
    loaded_at           TIMESTAMP,

    PRIMARY KEY (symbol_key, date_key)
) USING iceberg PARTITIONED BY (years(loaded_at));
```

#### `gold.fact_anomaly_daily` — Anomaly Events Fact Table

**Grain:** 1 row = 1 anomaly event (1 symbol × 1 detection timestamp)

```sql
CREATE TABLE gold.fact_anomaly_daily (
    -- Surrogate Keys (FK to dimensions)
    anomaly_id          BIGSERIAL     PRIMARY KEY,
    symbol_key          INTEGER       NOT NULL,      -- FK → dim_symbol
    date_key            INTEGER       NOT NULL,      -- FK → dim_date (YYYYMMDD)
    anomaly_type_key    INTEGER       NOT NULL,      -- FK → dim_anomaly_type
    news_category_key   INTEGER,                     -- FK → dim_news_category (filled by LLM)

    -- Detection Context
    detected_at         TIMESTAMP     NOT NULL,
    granularity         VARCHAR(5)    DEFAULT '1d',  -- "1m", "1d"
    severity            VARCHAR(10),                 -- "LOW", "MEDIUM", "HIGH"
    triggered_rules     VARCHAR[],                   -- Array of rule_code strings

    -- Measures — Raw Metrics at Detection Time
    price               DOUBLE,
    daily_return        DOUBLE,
    price_zscore        DOUBLE,
    volume              BIGINT,
    volume_zscore       DOUBLE,
    volume_ratio_20d    DOUBLE,
    intraday_range_pct  DOUBLE,
    vwap_deviation_pct  DOUBLE,
    rsi_14              DOUBLE,
    bb_position         DOUBLE,

    -- Layer 1 (LLM) Output
    llm_judgement       VARCHAR(30),                 -- "NEWS_EXPLAINED", "DATA_ERROR", "UNEXPLAINED"
    llm_explanation     TEXT,
    news_articles_found INTEGER,
    llm_processed_at    TIMESTAMP,

    -- Portfolio Flag
    portfolio_flag      BOOLEAN       DEFAULT FALSE, -- TRUE if symbol in user's watchlist

    -- Alert Delivery
    alert_sent          BOOLEAN       DEFAULT FALSE,
    alert_sent_at       TIMESTAMP
) USING iceberg PARTITIONED BY (days(detected_at));
```

#### `gold.fact_alert_history` — Alert Delivery Fact Table

**Grain:** 1 row = 1 Telegram alert delivery event

```sql
CREATE TABLE gold.fact_alert_history (
    -- Surrogate Keys
    alert_id            BIGSERIAL     PRIMARY KEY,
    anomaly_id          BIGINT        NOT NULL,      -- FK → fact_anomaly_daily
    symbol_key          INTEGER       NOT NULL,      -- FK → dim_symbol (denormalized for perf)
    date_key            INTEGER       NOT NULL,      -- FK → dim_date

    -- Measures
    alerted_at          TIMESTAMP     NOT NULL,
    delivery_channel    VARCHAR(20)   DEFAULT 'telegram',
    delivery_status     VARCHAR(20),                 -- "DELIVERED", "FAILED", "PENDING"
    llm_judgement       VARCHAR(30),
    severity            VARCHAR(10),

    -- Telegram specific
    telegram_msg_id     BIGINT,
    telegram_chat_id    BIGINT,

    -- Acknowledge (future feature)
    acknowledged_at     TIMESTAMP,
    acknowledged_by     VARCHAR(100)
) USING iceberg PARTITIONED BY (days(alerted_at));
```

---

### 4.5 Context Table — `gold.rule_engine_context`

Bảng này không thuộc Star Schema (là operational table, không phải analytical). Dùng bởi rule engine real-time.

**Grain:** 1 row = 1 symbol × 1 trading day (snapshot của rolling stats)

```sql
CREATE TABLE gold.rule_engine_context (
    symbol              VARCHAR(20)   NOT NULL,
    as_of_date          DATE          NOT NULL,

    -- Rolling return stats (20-day)
    mean_return_20d     DOUBLE,
    std_return_20d      DOUBLE,
    mean_return_5d      DOUBLE,
    std_return_5d       DOUBLE,

    -- Rolling volume stats
    mean_volume_20d     DOUBLE,
    std_volume_20d      DOUBLE,
    mean_volume_5d      DOUBLE,

    -- Technical indicator baselines
    bb_upper_20d        DOUBLE,
    bb_lower_20d        DOUBLE,
    bb_mid_20d          DOUBLE,
    atr_14              DOUBLE,
    rsi_14              DOUBLE,
    vwap_5d_avg         DOUBLE,

    updated_at          TIMESTAMP,
    PRIMARY KEY (symbol, as_of_date)
) USING iceberg PARTITIONED BY (months(as_of_date));
```

**Spark job `build_rule_context` (daily, 07:00 UTC):**

```python
from pyspark.sql import functions as F
from pyspark.sql.window import Window

# Load silver data
df = spark.read.format("iceberg").load("iceberg.silver.ohlcv_daily")

# Window for rolling stats
w20 = Window.partitionBy("symbol").orderBy("trade_date").rowsBetween(-19, 0)
w5  = Window.partitionBy("symbol").orderBy("trade_date").rowsBetween(-4, 0)

context = df.withColumn("mean_return_20d", F.avg("daily_return").over(w20)) \
             .withColumn("std_return_20d",  F.stddev("daily_return").over(w20)) \
             .withColumn("mean_volume_20d", F.avg("volume").over(w20)) \
             .withColumn("std_volume_20d",  F.stddev("volume").over(w20)) \
             .withColumn("mean_return_5d",  F.avg("daily_return").over(w5)) \
             .withColumn("bb_mid_20d",      F.avg("close").over(w20)) \
             .withColumn("bb_std_20d",      F.stddev("close").over(w20)) \
             .withColumn("bb_upper_20d",    F.col("bb_mid_20d") + 2 * F.col("bb_std_20d")) \
             .withColumn("bb_lower_20d",    F.col("bb_mid_20d") - 2 * F.col("bb_std_20d")) \
             .filter(F.col("trade_date") == F.current_date() - 1)

context.write.format("iceberg") \
    .mode("overwrite") \
    .option("path", "s3a://warehouse/gold/rule_engine_context") \
    .save()
```

---

### 4.6 Sample Trino Queries trên Star Schema

**Query 1: Top 10 mã có anomaly UNEXPLAINED trong tuần này (User Scenario A)**

```sql
SELECT s.symbol, s.company_name, s.sector,
       COUNT(*) AS anomaly_count,
       AVG(ABS(a.daily_return)) * 100 AS avg_abs_return_pct,
       MAX(a.severity) AS max_severity
FROM gold.fact_anomaly_daily a
JOIN gold.dim_symbol s ON a.symbol_key = s.symbol_key
JOIN gold.dim_date d ON a.date_key = d.date_key
WHERE a.llm_judgement = 'UNEXPLAINED'
  AND d.week_of_year = WEEK(CURRENT_DATE)
  AND d.year = YEAR(CURRENT_DATE)
  AND s.is_active = TRUE
GROUP BY s.symbol, s.company_name, s.sector
ORDER BY anomaly_count DESC
LIMIT 10;
```

**Query 2: Anomaly impact by news category (User Scenario D)**

```sql
SELECT nc.category_name,
       COUNT(*) AS event_count,
       AVG(ABS(a.daily_return)) * 100 AS avg_move_pct,
       AVG(a.volume_ratio_20d) AS avg_vol_ratio
FROM gold.fact_anomaly_daily a
JOIN gold.dim_news_category nc ON a.news_category_key = nc.category_key
JOIN gold.dim_date d ON a.date_key = d.date_key
WHERE d.year = 2026
GROUP BY nc.category_name
ORDER BY avg_move_pct DESC;
```

**Query 3: Portfolio risk summary (User Scenario B)**

```sql
SELECT s.symbol, s.sector,
       COUNT(CASE WHEN a.llm_judgement = 'UNEXPLAINED' THEN 1 END) AS unexplained_count,
       COUNT(CASE WHEN a.llm_judgement = 'NEWS_EXPLAINED' THEN 1 END) AS explained_count,
       MAX(ABS(a.daily_return)) * 100 AS max_move_pct
FROM gold.fact_anomaly_daily a
JOIN gold.dim_symbol s ON a.symbol_key = s.symbol_key
WHERE a.portfolio_flag = TRUE
  AND a.detected_at >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY s.symbol, s.sector
ORDER BY unexplained_count DESC;
```

---

## 5. Rule-Based Detection Engine (Layer 0)

### 5.1 Bộ 6 Rules

#### R1: Price Z-Score

\[ z*{price} = \frac{r_t - \bar{r}*{20d}}{\sigma\_{r,20d}} \]

Trigger: \( |z\_{price}| > 3.0 \) | Severity: HIGH nếu \( |z| > 4.5 \), MEDIUM nếu trong \( (3.0, 4.5] \)

#### R2: Volume Z-Score

\[ z*{vol} = \frac{V_t - \bar{V}*{20d}}{\sigma\_{V,20d}} \]

Trigger: \( z\_{vol} > 3.0 \) | Severity: HIGH nếu \( z > 5.0 \), MEDIUM nếu trong \( (3.0, 5.0] \)

#### R3: Volume Ratio (backup khi std_volume ≈ 0)

\[ ratio*{vol} = \frac{V_t}{\bar{V}*{20d}} \]

Trigger: \( ratio\_{vol} > 3.5 \) | Severity: MEDIUM

#### R4: Bollinger Band Breakout

\[ bb*{pos} = \frac{close - BB*{lower}}{BB*{upper} - BB*{lower}} \]

Trigger: \( bb*{pos} > 1.0 \) hoặc \( bb*{pos} < 0.0 \) | Severity: MEDIUM

#### R5: RSI Extreme

Trigger: \( RSI*{14} > 80 \) hoặc \( RSI*{14} < 20 \) | Severity: LOW (đơn lẻ), MEDIUM nếu kết hợp R1/R4

#### R6: Intraday Range

\[ range\_{pct} = \frac{high_t - low_t}{low_t} \]

Trigger: \( range\_{pct} > 0.05 \) (>5%) | Severity: MEDIUM

---

## 6. LLM Agent Workflow — Layer 1 Validation (LangGraph)

### 6.1 State Schema

```python
class AnomalyAgentState(TypedDict):
    symbol: str
    anomaly_type: str
    severity: str
    triggered_rules: list
    price_zscore: float
    volume_zscore: float
    volume_ratio_20d: float
    daily_return: float
    rsi_14: float
    bb_position: float
    detected_at: str
    # Intermediates
    formatted_question: Optional[str]
    news_articles: Optional[List[dict]]
    news_judgement: Optional[str]      # EXPLAINED / UNEXPLAINED / UNCERTAIN
    news_reasoning: Optional[str]
    crosscheck_result: Optional[str]   # DATA_OK / DATA_ERROR
    crosscheck_detail: Optional[str]
    # Finals
    final_judgement: Optional[str]     # NEWS_EXPLAINED / DATA_ERROR / UNEXPLAINED
    final_explanation: Optional[str]
    alert_message: Optional[str]
    should_alert: bool
    news_category: Optional[str]       # LLM-assigned category for dim_news_category
```

### 6.2 Graph Flow

```
START → [data_conversion] → [news_research ‖ data_crosscheck]
      → [aggregation] → conditional:
            "DATA_ERROR"      → [discard]    → END
            "NEWS_EXPLAINED"  → [log_only]   → END
            "UNEXPLAINED"     → [alert]      → END
```

### 6.3 Node Chính

**data_conversion_node**: Format metrics thành ngôn ngữ tự nhiên để LLM hiểu context.[^7]

**news_research_node** (parallel):

- Fetch từ NewsAPI + Finnhub Company News (last 6h)
- Gọi Gemini 2.5 Flash-Lite: "Does any news explain this anomaly?"
- Output: `news_judgement` (EXPLAINED/UNEXPLAINED/UNCERTAIN) + `news_category` (earnings/macro/none/...)

**data_crosscheck_node** (parallel):

- Verify price cross Finnhub vs yfinance
- If discrepancy >10% → `DATA_ERROR`[^9]

**aggregation_node** + conditional routing:

- `DATA_ERROR` → discard (không tốn token Telegram)
- `NEWS_EXPLAINED` → log_only (không alert, chỉ lưu record với news_category)
- `UNEXPLAINED` → alert (gửi Telegram)

**alert_node**: Format Telegram message với symbol, metrics, AI explanation + `#UNEXPLAINED #TICKER #TYPE`

---

## 7. Alert Generation Flow — End-to-End

Phần này mô tả toàn bộ luồng từ khi dữ liệu tời từ nguồn cho đến khi Telegram alert bắn ra tới tay end user.[^19][^20][^21]

### 7.1 Sơ đồ luồng tổng thể (No TimescaleDB)

```
┌────────────────────────────────────────────────────────────────────────────┐
│  PHASE 0: CONTEXT PREPARATION (Spark Batch — daily 07:00 UTC)        │
│                                                                        │
│  silver.ohlcv_daily  ─────────────────────────► gold.rule_engine_context  │
│  (cleaned OHLCV history)     (Spark Window Functions)   (mean,std,     │
│                                                          BB,RSI,ATR...) │
│  Rule Engine service startup: pre-load context vào memory (HashMap)   │
│  Refresh mỗi ngày 07:00 UTC trước giờ mở NYSE                        │
└────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: INGESTION (Real-time, 09:30–16:00 ET)                       │
│                                                                        │
│  [yfinance WebSocket]      [Finnhub WebSocket]    [NewsAPI Poller]     │
│  quote: AAPL $178.52       trades: AAPL 100sh     articles (5min)     │
│       │                         │                      │               │
│       ▼                         ▼                      ▼               │
│  [FastStream Producer]   [FastStream Producer]   [FastStream Producer] │
│       │                         │                      │               │
│       ▼                         ▼                      ▼               │
│  raw.stock.quotes         raw.stock.trades        raw.stock.news        │
│  [Kafka, NO DB]           [Kafka, NO DB]          [Kafka → Spark →     │
│                                                    Iceberg Bronze]      │
└────────────────────────────────────────────────────────────────────────────┘
                │                          │
                ▼                          ▼ (Spark Structured Streaming)
                │                   silver.ohlcv_1min (Iceberg)
                │
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PHASE 3: LAYER 0 — RULE ENGINE (FastStream Consumer)               │
│                                                                        │
│  Consumer: raw.stock.quotes ────────────────────────────────────┐  │
│  │                                                                  │  │
│  ▼                                                                  │  │
│  [^1] Lấy context:                                                   │  │
│      read gold.rule_engine_context[symbol, today]                   │  │
│      (mean_return_20d, std_return_20d, mean_vol_20d, bb_*, rsi...)  │  │
│                                                                      │  │
│  [^2] Compute metrics (từ Kafka event + in-memory context):           │  │
│      return_t  = (price - prev_close) / prev_close                  │  │
│      price_z   = (return_t - mean_return_20d) / std_return_20d      │  │
│      vol_z     = (day_volume - mean_vol_20d) / std_vol_20d          │  │
│      vol_ratio = day_volume / mean_vol_20d                          │  │
│      bb_pos    = (price - bb_lower_20d) / (bb_upper_20d - bb_lower) │  │
│      range_pct = (day_high - day_low) / day_low                     │  │
│      [Context được pre-load vào HashMap khi service start,           │  │
│       không có DB query trong hot path]                             │  │
│                                                                      │  │
│  [^3] Apply 6 Rules:                                                  │  │
│      R1 PRICE_Z:     |price_z| > 3.0                                 │  │
│      R2 VOLUME_Z:    vol_z > 3.0                                     │  │
│      R3 VOLUME_RATIO: vol_ratio > 3.5                                │  │
│      R4 BB_BREAKOUT: bb_pos > 1.0 OR bb_pos < 0.0                   │  │
│      R5 RSI_EXTREME: rsi > 80 OR rsi < 20                           │  │
│      R6 INTRADAY_RANGE: range_pct > 0.05                            │  │
│                                                                      │  │
│  [^4] Severity Aggregation:                                           │  │
│      0 rules hit          ──── PASS (discard, no event)            │  │
│      1 rule MEDIUM        ──── LOW                                 │  │
│      2+ rules MEDIUM      ──── escalate → HIGH                    │  │
│      any HIGH threshold   ──── HIGH                                │  │
│                                                                      │  │
│  [^5] Ngoại lệ vuợt ngưỡng LOW severity → discard                    │  │
│      Severity = MEDIUM/HIGH ────────────────────────────────►│  │
│                                                                      ▼  │
│  [^6] Publish: Kafka topic → alerts.raw                              │  │
│      {symbol, anomaly_type, severity, triggered_rules, metrics...}  │  │
│                                                                      │  │
│  [^7] Persist: INSERT → gold.fact_anomaly_daily                      │  │
│      (alert_sent=FALSE, llm_judgement=NULL — pending LLM)           │  │
└────────────────────────────────────────────────────────────────────────────┘
                                                  │
                                   Kafka: alerts.raw
                                                  │
                                                  ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PHASE 4: LAYER 1 — LLM AGENT (LangGraph, FastStream Consumer)     │
│                                                                        │
│  Consumer: alerts.raw                                                  │
│  │                                                                     │
│  ▼                                                                     │
│  [Node 1] data_conversion_node                                         │
│      Format metrics thành natural language:                            │
│      "NVDA: price z-score +4.21σ, volume 4.2x average,                 │
│       RSI 71.3, price broke above Bollinger upper band"                │
│                                                                        │
│  │         (LangGraph parallel branches)                               │
│  ├────────────────────────────────────────────────────┐               │
│  │                                                       │               │
│  ▼                                                       ▼               │
│  [Node 2a] news_research_node               [Node 2b] data_crosscheck  │
│  - Query Trino: bronze.raw_news_articles   - Compare price trong       │
│    WHERE symbol=X AND published_at            Kafka event với         │
│    >= NOW() - INTERVAL '6' HOUR              gold.rule_engine_context  │
│  - (Một nguồn duy nhất: NewsAPI)            (prev_close, bb_upper/low)│
│  - Call Gemini 2.5 Flash-Lite:             - Nếu price deviation >10%  │
│    "Does news explain this anomaly?"          so với context →         │
│  - Output: news_judgement                    flag DATA_ERROR           │
│    (EXPLAINED / UNEXPLAINED)               - Output: crosscheck_result  │
│  - Output: news_category                    (DATA_OK / DATA_ERROR)    │
│    (earnings/macro/m_and_a/none/...)                                   │
│  │                                                       │               │
│  └───────────────────────────▼───────────────────────┘               │
│                            │                                            │
│                            ▼                                            │
│  [Node 3] aggregation_node                                              │
│  Aggregation + conditional routing:                                     │
│                                                                        │
│    ┌──────────────────────────────────────────────────────┐  │
│    │  crosscheck = DATA_ERROR?                          │  │
│    │  │                                                 │  │
│    │  ├─── YES ─► [discard_node]                         │  │
│    │  │         UPDATE fact_anomaly_daily                │  │
│    │  │         SET llm_judgement='DATA_ERROR'            │  │
│    │  │         alert_sent=FALSE                          │  │
│    │  │         END (no Telegram)                         │  │
│    │  │                                                 │  │
│    │  └─── NO ─► news_judgement = EXPLAINED?             │  │
│    │              │                                      │  │
│    │              ├─── YES ─► [log_only_node]            │  │
│    │              │         UPDATE fact_anomaly_daily     │  │
│    │              │         SET llm_judgement             │  │
│    │              │          ='NEWS_EXPLAINED'             │  │
│    │              │         news_category = 'earnings'... │  │
│    │              │         alert_sent=FALSE              │  │
│    │              │         END (no Telegram)             │  │
│    │              │                                      │  │
│    │              └─── NO ─► [alert_node]               │  │
│    │                        UPDATE fact_anomaly_daily     │  │
│    │                        SET llm_judgement             │  │
│    │                          ='UNEXPLAINED'               │  │
│    │                        news_category = 'none'        │  │
│    │                        alert_sent=TRUE               │  │
│    └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
                                                  │
                           Kafka: alerts.confirmed (UNEXPLAINED only)
                                                  │
                                                  ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PHASE 5: DELIVERY (Telegram Bot)                                    │
│                                                                        │
│  Consumer: alerts.confirmed                                            │
│  │                                                                     │
│  ▼                                                                     │
│  [Format Telegram Message]                                             │
│  - Build message string (symbol, metrics, AI explanation)              │
│  - Attach tags: #UNEXPLAINED #NVDA #VOLUME_SPIKE                       │
│                                                                        │
│  [Send via python-telegram-bot]                                        │
│  - Target: chat_id per user group / watchlist                          │
│  - Log: INSERT gold.fact_alert_history                                 │
│         {alert_id, anomaly_id, alerted_at, delivery_status}           │
│                                                                        │
│                  ▼                                                     │
│        [End User — Telegram]                                           │
│        Nhận alert với full context                                    │
└────────────────────────────────────────────────────────────────────────────┘
```

---

### 7.2 Kafka Topics và Message Schema

**5 Kafka topics trong hệ thống:**

| Topic              | Producer               | Consumer                                            | Định dạng message     | Mục đích                                                      |
| ------------------ | ---------------------- | --------------------------------------------------- | --------------------- | ------------------------------------------------------------- |
| `raw.stock.quotes` | yfinance WS producer   | Rule Engine (FastStream)                            | JSON (QuoteEvent)     | Level-1 quote feed — **Kafka-only, no DB**                    |
| `raw.stock.trades` | Finnhub WS producer    | Spark Structured Streaming                          | JSON (TradeEvent)     | Tick feed → aggregate 1m bars → `silver.ohlcv_1min` (Iceberg) |
| `raw.stock.news`   | NewsAPI poller         | Spark writer → `bronze.raw_news_articles` (Iceberg) | JSON (NewsEvent)      | News feed duy nhất (NewsAPI)                                  |
| `alerts.raw`       | Rule Engine            | LLM Agent (FastStream Consumer)                     | JSON (RawAlert)       | Rule-triggered events chưa validate                           |
| `alerts.confirmed` | LLM Agent (alert_node) | Telegram Bot                                        | JSON (ConfirmedAlert) | UNEXPLAINED alerts đã xác nhận                                |

**Message schema `alerts.raw`:**

```json
{
  "anomaly_id": "uuid-v4",
  "symbol": "NVDA",
  "detected_at": "2026-03-26T10:23:15Z",
  "severity": "HIGH",
  "triggered_rules": ["VOLUME_Z", "VOLUME_RATIO", "BB_BREAKOUT"],
  "price": 178.52,
  "daily_return": 0.0312,
  "price_zscore": 2.1,
  "volume": 82000000,
  "volume_zscore": 4.21,
  "volume_ratio_20d": 3.8,
  "intraday_range_pct": 0.063,
  "rsi_14": 71.3,
  "bb_position": 1.03,
  "portfolio_flag": false
}
```

**Message schema `alerts.confirmed`:**

```json
{
  "anomaly_id": "uuid-v4",
  "symbol": "NVDA",
  "detected_at": "2026-03-26T10:23:15Z",
  "severity": "HIGH",
  "triggered_rules": ["VOLUME_Z", "VOLUME_RATIO", "BB_BREAKOUT"],
  "llm_judgement": "UNEXPLAINED",
  "llm_explanation": "No major news found for NVDA in the last 6 hours. Volume surge appears unexplained by public information.",
  "news_category": "none",
  "news_articles_found": 0,
  "price": 178.52,
  "daily_return": 0.0312,
  "price_zscore": 2.1,
  "volume_zscore": 4.21,
  "volume_ratio_20d": 3.8,
  "rsi_14": 71.3,
  "bb_position": 1.03
}
```

---

### 7.3 FastStream Code Skeleton — Rule Engine Consumer

Dựa trên FastStream framework cho Kafka consumer/producer:[^21][^22][^23]

```python
from faststream import FastStream
from faststream.kafka import KafkaBroker
from pydantic import BaseModel
from typing import List, Optional
import asyncpg  # TimescaleDB connection

broker = KafkaBroker("redpanda:9092")
app = FastStream(broker)

class QuoteEvent(BaseModel):
    symbol: str
    price: float
    change_pct: float
    day_volume: int
    day_high: float
    day_low: float
    prev_close: float
    event_ts: str

class RawAlert(BaseModel):
    anomaly_id: str
    symbol: str
    detected_at: str
    severity: str
    triggered_rules: List[str]
    price: float
    daily_return: float
    price_zscore: float
    volume: int
    volume_zscore: float
    volume_ratio_20d: float
    intraday_range_pct: float
    rsi_14: Optional[float]
    bb_position: Optional[float]
    portfolio_flag: bool

async def get_context(symbol: str) -> dict:
    """Lấy rule_engine_context từ TimescaleDB"""
    conn = await asyncpg.connect("postgresql://...")
    row = await conn.fetchrow(
        "SELECT * FROM gold.rule_engine_context "
        "WHERE symbol=$1 AND as_of_date = CURRENT_DATE - 1",
        symbol
    )
    await conn.close()
    return dict(row) if row else {}

def apply_rules(metrics: dict, ctx: dict) -> tuple[List[str], str]:
    """Apply 6 rules, trả về (triggered_rules, severity)"""
    triggered = []
    # R1 PRICE_Z
    if abs(metrics["price_z"]) > 3.0:
        triggered.append("PRICE_Z")
    # R2 VOLUME_Z
    if metrics["vol_z"] > 3.0:
        triggered.append("VOLUME_Z")
    # R3 VOLUME_RATIO
    if metrics["vol_ratio"] > 3.5:
        triggered.append("VOLUME_RATIO")
    # R4 BB_BREAKOUT
    if ctx.get("bb_upper_20d") and ctx.get("bb_lower_20d"):
        bb_pos = (metrics["price"] - ctx["bb_lower_20d"]) / \
                 (ctx["bb_upper_20d"] - ctx["bb_lower_20d"])
        if bb_pos > 1.0 or bb_pos < 0.0:
            triggered.append("BB_BREAKOUT")
    # R5 RSI_EXTREME
    if ctx.get("rsi_14") and (ctx["rsi_14"] > 80 or ctx["rsi_14"] < 20):
        triggered.append("RSI_EXTREME")
    # R6 INTRADAY_RANGE
    if metrics["range_pct"] > 0.05:
        triggered.append("INTRADAY_RANGE")
    # Severity
    if len(triggered) == 0:
        return triggered, "PASS"
    high_rules = {"PRICE_Z", "VOLUME_Z"}
    is_high = any(r in high_rules for r in triggered) and abs(metrics.get("price_z", 0)) > 4.5
    severity = "HIGH" if (is_high or len(triggered) >= 2) else "LOW"
    return triggered, severity

@broker.publisher("alerts.raw")
@broker.subscriber("raw.stock.quotes")
async def process_quote(event: QuoteEvent) -> Optional[RawAlert]:
    ctx = await get_context(event.symbol)
    if not ctx:
        return None  # Chưa có context, bỏ qua

    metrics = {
        "price": event.price,
        "price_z": (event.change_pct / 100 - ctx["mean_return_20d"]) / \
                    max(ctx["std_return_20d"], 1e-6),
        "vol_z": (event.day_volume - ctx["mean_volume_20d"]) / \
                  max(ctx["std_volume_20d"], 1e-6),
        "vol_ratio": event.day_volume / max(ctx["mean_volume_20d"], 1),
        "range_pct": (event.day_high - event.day_low) / max(event.day_low, 1e-6),
    }

    triggered_rules, severity = apply_rules(metrics, ctx)

    if severity in ("PASS", "LOW"):
        return None  # Filter, không publish

    import uuid
    return RawAlert(
        anomaly_id=str(uuid.uuid4()),
        symbol=event.symbol,
        detected_at=event.event_ts,
        severity=severity,
        triggered_rules=triggered_rules,
        price=event.price,
        daily_return=event.change_pct / 100,
        price_zscore=round(metrics["price_z"], 3),
        volume=event.day_volume,
        volume_zscore=round(metrics["vol_z"], 3),
        volume_ratio_20d=round(metrics["vol_ratio"], 2),
        intraday_range_pct=round(metrics["range_pct"], 4),
        rsi_14=ctx.get("rsi_14"),
        bb_position=None,
        portfolio_flag=False
    )
```

---

### 7.4 LangGraph Agent Code Skeleton — Layer 1

Dựa trên LangGraph conditional edges và parallel branches:[^10][^24][^25]

```python
from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Optional, List
import asyncio

class AnomalyAgentState(TypedDict):
    # Input từ alerts.raw
    anomaly_id: str
    symbol: str
    severity: str
    triggered_rules: List[str]
    price_zscore: float
    volume_zscore: float
    volume_ratio_20d: float
    daily_return: float
    rsi_14: Optional[float]
    bb_position: Optional[float]
    detected_at: str
    # Intermediate state
    formatted_context: Optional[str]
    news_articles: Optional[List[dict]]
    news_judgement: Optional[str]       # EXPLAINED / UNEXPLAINED / UNCERTAIN
    news_reasoning: Optional[str]
    news_category: Optional[str]        # earnings / macro / m_and_a / none / ...
    crosscheck_result: Optional[str]    # DATA_OK / DATA_ERROR
    # Final
    final_judgement: Optional[str]      # NEWS_EXPLAINED / DATA_ERROR / UNEXPLAINED
    final_explanation: Optional[str]
    should_alert: bool

async def data_conversion_node(state: AnomalyAgentState) -> AnomalyAgentState:
    """Format số thành ngôn ngữ tự nhiên"""
    ctx = (
        f"{state['symbol']}: price z-score {state['price_zscore']:+.2f}σ, "
        f"volume {state['volume_ratio_20d']:.1f}x average (z={state['volume_zscore']:+.2f}σ), "
        f"daily return {state['daily_return']*100:+.2f}%, "
        f"RSI={state['rsi_14'] or 'N/A'}, "
        f"rules triggered: {', '.join(state['triggered_rules'])}"
    )
    state["formatted_context"] = ctx
    return state

async def news_research_node(state: AnomalyAgentState) -> AnomalyAgentState:
    """Fetch news + Gemini validation (parallel branch A)"""
    # 1. Fetch từ Kafka raw.stock.news (query recent 6h) + Finnhub REST
    news = await fetch_recent_news(state["symbol"], hours=6)
    state["news_articles"] = news
    # 2. Gemini 2.5 Flash-Lite call
    from google.generativeai import GenerativeModel
    model = GenerativeModel("gemini-2.5-flash-lite")
    prompt = (
        f"Financial anomaly detected: {state['formatted_context']}\n"
        f"Recent news (last 6h): {[n['headline'] for n in news[:5]]}\n\n"
        f"Question: Does any of this news adequately explain the anomaly?\n"
        f"Answer with: EXPLAINED or UNEXPLAINED. Then provide the news category "
        f"(earnings/macro/m_and_a/analyst_rating/regulation/none) and a 1-sentence reason."
    )
    resp = model.generate_content(prompt)
    text = resp.text.strip()
    # Parse response
    state["news_judgement"] = "EXPLAINED" if "EXPLAINED" in text.split("\n") else "UNEXPLAINED"
    state["news_category"] = extract_category(text)
    state["news_reasoning"] = text
    return state

async def data_crosscheck_node(state: AnomalyAgentState) -> AnomalyAgentState:
    """Cross-check data quality (parallel branch B)"""
    # So sánh price với prev_close từ 2 nguồn khác nhau
    # Nếu deviation > 10% → DATA_ERROR
    state["crosscheck_result"] = "DATA_OK"  # Logic thực tế gọi TimescaleDB
    return state

async def aggregation_node(state: AnomalyAgentState) -> AnomalyAgentState:
    """Merge kết quả 2 branch"""
    if state["crosscheck_result"] == "DATA_ERROR":
        state["final_judgement"] = "DATA_ERROR"
        state["final_explanation"] = "Data feed discrepancy detected, skipping alert."
        state["should_alert"] = False
    elif state["news_judgement"] == "EXPLAINED":
        state["final_judgement"] = "NEWS_EXPLAINED"
        state["final_explanation"] = state["news_reasoning"]
        state["should_alert"] = False
    else:
        state["final_judgement"] = "UNEXPLAINED"
        state["final_explanation"] = state["news_reasoning"]
        state["should_alert"] = True
    return state

def route_after_aggregation(state: AnomalyAgentState) -> str:
    if state["final_judgement"] == "DATA_ERROR":
        return "discard"
    elif state["final_judgement"] == "NEWS_EXPLAINED":
        return "log_only"
    else:
        return "alert"

async def alert_node(state: AnomalyAgentState) -> AnomalyAgentState:
    """Format + gửi Telegram"""
    msg = format_telegram_message(state)
    await send_telegram(msg)
    await update_fact_anomaly(state["anomaly_id"], state)
    return state

# Build LangGraph
workflow = StateGraph(AnomalyAgentState)
workflow.add_node("data_conversion", data_conversion_node)
workflow.add_node("news_research", news_research_node)
workflow.add_node("data_crosscheck", data_crosscheck_node)
workflow.add_node("aggregation", aggregation_node)
workflow.add_node("discard", lambda s: s)   # no-op, log only
workflow.add_node("log_only", lambda s: s)  # no-op, just update DB
workflow.add_node("alert", alert_node)

workflow.add_edge(START, "data_conversion")
workflow.add_edge("data_conversion", "news_research")    # parallel
workflow.add_edge("data_conversion", "data_crosscheck")  # parallel
workflow.add_edge("news_research", "aggregation")
workflow.add_edge("data_crosscheck", "aggregation")
workflow.add_conditional_edges(
    "aggregation",
    route_after_aggregation,
    {"discard": "discard", "log_only": "log_only", "alert": "alert"}
)
workflow.add_edge("discard", END)
workflow.add_edge("log_only", END)
workflow.add_edge("alert", END)

agent = workflow.compile()
```

---

### 7.5 Latency Budget — Từ Quote đến Telegram

| Giai đoạn                        | Thành phần                              | Latency ước tính |
| -------------------------------- | --------------------------------------- | ---------------- |
| yfinance WS → Kafka              | WebSocket latency + FastStream publish  | ~50–200ms        |
| Rule Engine processing           | Context lookup + 6 rules                | ~10–50ms         |
| Kafka publish `alerts.raw`       | Redpanda write                          | ~5–10ms          |
| LLM Agent — news fetch           | NewsAPI + Finnhub REST 2 calls parallel | ~500ms–1s        |
| LLM Agent — Gemini call          | Flash-Lite inference                    | ~1–3s            |
| Kafka publish `alerts.confirmed` | Redpanda write                          | ~5ms             |
| Telegram delivery                | python-telegram-bot API                 | ~200–500ms       |
| **Tổng end-to-end**              |                                         | **~2–5 giây**    |

> **Target SLA: < 10 giây** từ khi quote đến khi user nhận Telegram. Trong thực tế, bước chận nhất là Gemini call (~1–3s) và news fetching (~0.5–1s).

---

## 8. Input / Output cho End User

### 7.1 User-facing Interfaces

| Interface         | Access         | Nội dung                                                               |
| ----------------- | -------------- | ---------------------------------------------------------------------- |
| **Telegram Bot**  | Real-time push | Alert khi anomaly MEDIUM/HIGH + AI explanation                         |
| **Grafana Panel** | Web dashboard  | Alert timeline, anomaly frequency chart, severity heatmap              |
| **Superset**      | Web analytics  | Historical anomaly browser, news-category breakdown, portfolio summary |

### 7.2 Telegram Alert Format

```
🔴 [ANOMALY] NVDA — 2026-03-25 10:23
━━━━━━━━━━━━━━━━━━━━━━━
Type: VOLUME_SPIKE | Severity: HIGH
Rules: VOLUME_Z | VOLUME_RATIO | BB_BREAKOUT

📊 Metrics:
• Daily return:     +3.12%  (z = +2.1σ)
• Volume z-score:  +4.21σ  (3.8x avg)
• RSI(14):         71.3
• BB position:     1.03

🤖 AI Analysis (Gemini):
No major news found for NVDA in the last 6 hours.
This volume surge appears unexplained by public information
and may warrant further investigation.
━━━━━━━━━━━━━━━━━━━━━━━
#UNEXPLAINED #NVDA #VOLUME_SPIKE
```

---

## 8. System Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                    GKE CLUSTER (Zonal, us-central1-a)                │
│                                                                       │
│  NODE 1 (e2-standard-2, on-demand)                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  FastStream Service (Python)                                   │   │
│  │    Consumer: raw.stock.quotes → Rule Engine → alerts.raw      │   │
│  │    Consumer: alerts.raw → LangGraph Agent → alerts.confirmed  │   │
│  │    Producer: NewsAPI + Finnhub News polling (5 min)           │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  Trino Coordinator  │  Gravitino REST Catalog                │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  Grafana (Alert Panel)  │  Superset (Analytics Dashboard)    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  NODE 2 (e2-medium, on-demand)                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Redpanda (single-broker)                                     │   │
│  │    raw.stock.quotes | raw.stock.trades | raw.stock.news       │   │
│  │    alerts.raw | alerts.confirmed                              │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │  TimescaleDB: Bronze streaming tables                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  NODE 3 (e2-standard-4, SPOT)                                         │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Spark Operator (SparkApplication CronJobs)                   │   │
│  │    Daily 06:30 UTC: Bronze → Silver → Gold ETL               │   │
│  │    Daily 07:00 UTC: Rebuild gold.rule_engine_context          │   │
│  │    Hourly: silver.ohlcv_1min aggregation                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
┌──────────────────┐         ┌──────────────────────────┐
│  MinIO (PVC)     │         │  EXTERNAL                │
│  Iceberg files   │         │  Gemini 2.5 Flash-Lite   │
│  Gold/Silver     │         │  NewsAPI, Finnhub REST   │
└──────────────────┘         │  yfinance WS, AV REST    │
                             └──────────┬───────────────┘
                                        ▼
                             ┌──────────────────────────┐
                             │  Telegram Bot            │
                             └──────────────────────────┘
```

---

## 9. Spark Batch Jobs

| Job                      | Schedule        | Input → Output                                | Mô tả                                        |
| ------------------------ | --------------- | --------------------------------------------- | -------------------------------------------- |
| `etl_bronze_to_silver`   | Daily 06:30 UTC | Bronze Iceberg → Silver Iceberg               | Clean, deduplicate, fill gaps                |
| `build_rule_context`     | Daily 07:00 UTC | Silver ohlcv_daily → gold.rule_engine_context | Tính rolling stats cho toàn bộ symbols       |
| `build_gold_star_schema` | Daily 07:30 UTC | Silver → Gold fact/dim tables                 | Populate fact_ohlcv_daily, update dim_symbol |
| `agg_1min_bars`          | Hourly          | TimescaleDB raw_trades → silver.ohlcv_1min    | Aggregate tick → 1-min OHLCV                 |

---

## 10. Chi phí ước tính (3 tháng)

| Hạng mục                          | $/tháng | Ghi chú                             |
| --------------------------------- | ------- | ----------------------------------- |
| GKE management fee                | $0.00   | Free tier zonal[^26][^27]           |
| Node 1 (e2-standard-2, on-demand) | $48.91  |                                     |
| Node 2 (e2-medium, on-demand)     | $24.46  |                                     |
| Node 3 (e2-standard-4, Spot)      | $9.86   | Spark ETL[^28]                      |
| GCS + PD storage                  | $7.60   |                                     |
| Gemini 2.5 Flash-Lite             | $0.00   | ≤50 calls/day < free tier[^29][^30] |
| NewsAPI                           | $0.00   | Developer 100 req/day free[^18]     |
| Finnhub                           | $0.00   | Free 60/min[^16]                    |

| yfinance | $0.00 | Unofficial free |
| **Tổng/tháng** | **$90.83** | |
| **Tổng 3 tháng** | **$272.49** | Buffer: $27.51 |

---

## 11. Lộ trình Triển khai

### Phase 1 — Infrastructure & Bootstrap Data (Tuần 1–3)

- GKE cluster, MinIO, Gravitino, Redpanda, TimescaleDB deploy
- Alpha Vantage batch crawl: OVERVIEW + 20-year daily OHLCV (500 symbols)
- yfinance batch: 2-year daily + 2-year intraday 5min
- Spark ETL: Bronze → Silver → Gold (dim_symbol, dim_date pre-populated, fact_ohlcv_daily)
- Verify: Trino Star Schema queries hoạt động

### Phase 2 — Rule Engine (Tuần 4–5)

- yfinance WebSocket + Finnhub WS → Redpanda producers
- FastStream consumer: rule engine 6 rules
- Unit test từng rule với historical known anomalies (e.g., COVID crash 2020, Flash Crash 2010)
- E2E test: live quote → anomaly event in `gold.fact_anomaly_daily`

### Phase 3 — LLM Agent (Tuần 6–7)

- NewsAPI + Finnhub News polling → raw.stock.news
- LangGraph agent (4 nodes, parallel) integration
- Telegram bot
- E2E: rule trigger → Telegram alert với AI explanation in <30s

### Phase 4 — Dashboard & Demo (Tuần 8–10)

- Grafana: alert timeline từ TimescaleDB gold.fact_alert_history
- Superset: historical anomaly analytics via Trino trên Star Schema
- Demo scenarios: Simulate volume spike → full pipeline → Telegram alert
- Chuẩn bị demo data (replay historical anomalies nếu cần)

---

## References

1. [Anomaly Detection: Spot Market Irregularities in Real-Time](https://intrinio.com/blog/anomaly-detection-in-finance-identifying-market-irregularities-with-real-time-data) - Anomaly detection helps financial firms uncover unusual real-time price moves, trading volumes and v...

2. [GitHub - SamPom100/UnusualVolumeDetector: Gets the last 5 months of volume history for every ticker, and alerts you when a stock's volume exceeds 10 standard deviations from the mean within the last 3 days](https://github.com/SamPom100/UnusualVolumeDetector) - Gets the last 5 months of volume history for every ticker, and alerts you when a stock's volume exce...

3. [Volume Detection Arsenal...](https://chartswatcher.com/pages/blog/profit-from-unusual-volume-in-stocks) - ChartsWatcher blog: Discover proven strategies to profit from unusual volume in stocks. Learn how vo...

4. [Anomaly Detection and Risk Early Warning System for Financial ...](https://www.icck.org/article/html/tetai.2025.191759) - This study provides a more accurate and stable anomaly detection method for financial market risk ma...

5. [Anomalies that Speak: Detecting the Unseen in Stock Market ...](https://www.linkedin.com/pulse/anomalies-speak-detecting-unseen-stock-market-dr-partha-majumdar-wwdzc) - This insight has applications in algorithmic trading, sentiment-aware forecasting models, and policy...

6. [How Sentiment Indicators Improve Algorithmic Trading Performance](https://journals.sagepub.com/doi/10.1177/21582440251369559) - This study explores the hypothesis that sentiment indicators can enhance the performance of algorith...

7. [Enhancing Anomaly Detection in Financial Markets with an LLM ...](https://arxiv.org/html/2403.19735v1) - Enhancing Anomaly Detection in Financial Markets with an LLM-based Multi-Agent Framework ... arXiv:2...

8. [Enhancing Anomaly Detection in Financial Markets with an LLM ...](https://arxiv.org/abs/2403.19735) - [Submitted on 28 Mar 2024]. Title:Enhancing Anomaly Detection in Financial Markets with an LLM-based...

9. [aGeNtIc time series anomaly detection on your df with ... - GitHub](https://github.com/andrewm4894/anomaly-agent) - A powerful Python library for detecting anomalies in time series data using Large Language Models (L...

10. [Advanced LangGraph: Implementing Conditional Edges and Tool ...](https://dev.to/jamesli/advanced-langgraph-implementing-conditional-edges-and-tool-calling-agents-3pdn) - In the previous articles, we discussed the limitations of LCEL and AgentExecutor, as well as the...

11. [Building multi-agent systems with LangGraph](https://cwan.com/resources/blog/building-multi-agent-systems-with-langgraph/) - Introduction to LangGraph In the previous article, we explored the concept of multi-agent systems an...

12. [WebSocket — yfinance](https://ranaroussi.github.io/yfinance/reference/yfinance.websocket.html)

13. [How to Scrape Yahoo Finance Using Python and Other Tools](https://liveproxies.io/blog/how-to-scrape-yahoo-finance) - Learn how to scrape Yahoo Finance in 2025 with Python, Selenium, and APIs. Get stock prices, financi...

14. [WebSocket — yfinance - GitHub Pages](https://ranaroussi.github.io/yfinance/reference/api/yfinance.WebSocket.html)

15. [GitHub - Finnhub-Stock-API/finnhub-chainlink](https://github.com/Finnhub-Stock-API/finnhub-chainlink) - Contribute to Finnhub-Stock-API/finnhub-chainlink development by creating an account on GitHub.

16. [Exploring the finnhub.io API | IBKR Quant](https://www.interactivebrokers.com/campus/ibkr-quant-news/exploring-the-finnhub-io-api/) - Its offering includes stock, bond, crpto, and FX historical price data and real time trades and quot...

17. [Everything - Documentation](https://newsapi.org/docs/endpoints/everything)

18. [Pricing - News API](https://newsapi.org/pricing)

19. [Real-Time IoT Anomaly Detection with Kafka & PySpark - 2025 ...](https://www.lktechacademy.com/2025/10/building-real-time-anomaly-detection-iot-kafka-pyspark.html?m=1) - Build real-time IoT anomaly detection with Kafka & PySpark. Complete 2025 guide with code for statis...

20. [Building a Telegram Bot Powered by Kafka and ksqlDB](https://www.confluent.io/blog/building-a-telegram-bot-powered-by-kafka-and-ksqldb/) - We're going to build a simple system that captures Wi-Fi packets, processes them, and serves up on-d...

21. [FastStream: Python's framework for Efficient Message ...](https://dev.to/airtai/faststream-pythons-framework-for-efficient-message-queue-handling-3pd2) - Ever felt lost in the complexity of microservices and message queues like Kafka and RabbitMQ? FastSt...

22. [faststream](https://pypi.org/project/faststream/0.1.0rc0/) - FastStream: the simplest way to work with a messaging queues

23. [FastStream: a powerful and easy-to-use library for building ...](https://www.reddit.com/r/Python/comments/16pc38l/faststream_a_powerful_and_easytouse_library_for/) - FastStream simplifies the process of writing producers and consumers for message queues, handling al...

24. [Build an intelligent financial analysis agent with LangGraph ... - AWS](https://aws.amazon.com/blogs/machine-learning/build-an-intelligent-financial-analysis-agent-with-langgraph-and-strands-agents/) - This post describes an approach of combining three powerful technologies to illustrate an architectu...

25. [Langgraph Graph API Generates Unexpected Conditional Edge to END in v0.3.32+ Compared to v0.3.31 · Issue #4394 · langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/issues/4394) - Checked other resources This is a bug, not a usage question. For questions, please use GitHub Discus...

26. [Google Kubernetes Engine (GKE)](https://cloud.google.com/kubernetes-engine) - GKE is the industry's first fully managed Kubernetes service with full Kubernetes API, 4-way autosca...

27. [GKE Free Tier](https://www.devzero.io/blog/gke-pricing) - Understand the real cost of running GKE with a clear breakdown of pricing, deployment models, and pr...

28. [Google Compute Engine Machine Type e2-medium](https://gcloud-compute.com/e2-medium.html) - Google Compute Engine machine type e2-medium with 2 vCPU and 4 GB memory. Available in 42 Google Clo...

29. [Google Gemini API Free Tier 2026: Complete Limits Guide + 429 ...](https://yingtu.ai/en/blog/google-gemini-api-free-tier) - Complete guide to Google Gemini API free tier in 2026. Learn exact RPM/TPM/RPD limits for Flash and ...

30. [Gemini API Rate Limits 2026: Complete Developer Guide ...](https://blog.laozhang.ai/en/posts/gemini-api-rate-limits-guide) - Gemini API enforces rate limits across four dimensions: RPM, TPM, RPD, and IPM. Learn the exact limi...
