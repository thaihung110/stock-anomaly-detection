# Spark Streaming Apps — Plan V3.3

Tài liệu này mô tả thiết kế, schema, flow, và hướng dẫn vận hành cho 2 Spark Structured Streaming apps đầu tiên của hệ thống Stock Anomaly Detection V3.3.

---

## Tổng quan

| App | Topic source | Output table | Độ phức tạp |
|---|---|---|---|
| `news-ingest-stream` | `raw.stock.news` | `bronze.raw_news_articles` (Iceberg) | Dễ — ingest thẳng |
| `trades-ohlcv-stream` | `raw.stock.trades` | `silver.ohlcv_1min` (Iceberg) | Trung bình — window aggregation |

Cả 2 app viết bằng **Scala**, đóng gói fat-jar, deploy qua **SparkApplication CRD** (kubeflow spark-operator) trên GKE. Iceberg catalog kết nối qua **Gravitino REST catalog**.

---

## Kiến trúc

```
NewsAPI Poller  ──► Kafka: raw.stock.news   ──► news-ingest-stream  ──► bronze.raw_news_articles (Iceberg)
Finnhub WS      ──► Kafka: raw.stock.trades ──► trades-ohlcv-stream ──► silver.ohlcv_1min        (Iceberg)
```

---

## App 1: `news-ingest-stream`

### Mục tiêu

Consume JSON messages từ `raw.stock.news`, parse, deduplicate theo `article_id = md5(url)`, append vào `bronze.raw_news_articles`.

Không có aggregation hay window — đây là ingest thuần túy. Trigger mỗi 30 giây.

### Input — message schema `raw.stock.news`

```json
{
  "symbol": "NVDA",
  "source_name": "Reuters",
  "author": "John Doe",
  "title": "NVDA reports record revenue",
  "description": "...",
  "url": "https://reuters.com/...",
  "published_at": "2026-04-14T10:00:00Z",
  "content": "...",
  "search_query": "NVDA",
  "fetched_at": "2026-04-14T10:05:00Z"
}
```

### Output schema — `bronze.raw_news_articles`

```sql
CREATE TABLE bronze.raw_news_articles (
    article_id    VARCHAR(64)   NOT NULL,   -- md5(url)
    symbol        VARCHAR(20),
    source_name   VARCHAR(100),
    author        VARCHAR(200),
    title         TEXT          NOT NULL,
    description   TEXT,
    url           TEXT          NOT NULL,
    published_at  TIMESTAMP     NOT NULL,
    content       TEXT,
    search_query  VARCHAR(50),
    fetched_at    TIMESTAMP
) USING iceberg PARTITIONED BY (days(published_at));
```

### Flow xử lý

```
readStream (Kafka: raw.stock.news)
  │
  ▼
parse JSON → cast types
  │
  ▼
add article_id = md5(url)
  │
  ▼
withWatermark("published_at", "10 minutes")
  │
  ▼
dropDuplicates(["article_id"])   ← dedup trong watermark window
  │
  ▼
writeStream → Iceberg bronze.raw_news_articles
trigger: ProcessingTime("30 seconds")
outputMode: append
checkpoint: s3a://checkpoints/news-ingest-stream/
```

### Thông số vận hành

| Tham số | Giá trị |
|---|---|
| Trigger interval | 30 seconds |
| Watermark | 10 minutes trên `published_at` |
| Checkpoint | `s3a://checkpoints/news-ingest-stream/` |
| Driver resources | 1 core / 512Mi |
| Executor | 1 instance / 1 core / 1g |

---

## App 2: `trades-ohlcv-stream`

### Mục tiêu

Consume JSON trade ticks từ `raw.stock.trades` (Finnhub WebSocket), aggregate theo tumbling window 1 phút theo từng symbol, tính OHLCV + VWAP + trade_count, append vào `silver.ohlcv_1min`.

### Input — message schema `raw.stock.trades`

```json
{
  "symbol": "NVDA",
  "price": 178.45,
  "volume": 250,
  "timestamp_ms": 1743034995000,
  "conditions": ["1"]
}
```

### Output schema — `silver.ohlcv_1min`

```sql
CREATE TABLE silver.ohlcv_1min (
    symbol       VARCHAR(20)   NOT NULL,
    bar_ts       TIMESTAMP     NOT NULL,   -- window start (1-minute boundary)
    open         DOUBLE        NOT NULL,
    high         DOUBLE        NOT NULL,
    low          DOUBLE        NOT NULL,
    close        DOUBLE        NOT NULL,
    volume       BIGINT        NOT NULL,
    trade_count  INTEGER,
    vwap         DOUBLE
) USING iceberg PARTITIONED BY (days(bar_ts));
```

### Flow xử lý

```
readStream (Kafka: raw.stock.trades)
  │
  ▼
parse JSON
cast timestamp_ms (LongType) → bar_ts (TimestampType / 1000)
  │
  ▼
withWatermark("bar_ts", "5 minutes")
  │
  ▼
groupBy(
  symbol,
  window("bar_ts", "1 minute")   ← tumbling window
)
  │
  ▼
agg:
  open         = first(price)  ordered by bar_ts asc
  high         = max(price)
  low          = min(price)
  close        = last(price)   ordered by bar_ts asc
  volume       = sum(volume)
  trade_count  = count(*)
  vwap         = sum(price * volume) / sum(volume)
  │
  ▼
select window.start as bar_ts, symbol, open, high, low, close, volume, trade_count, vwap
  │
  ▼
writeStream → Iceberg silver.ohlcv_1min
trigger: ProcessingTime("60 seconds")
outputMode: append          ← chỉ emit window đã đóng sau watermark
checkpoint: s3a://checkpoints/trades-ohlcv-stream/
```

### Thông số vận hành

| Tham số | Giá trị |
|---|---|
| Trigger interval | 60 seconds |
| Window | Tumbling 1 phút trên `bar_ts` |
| Watermark | 5 minutes — cho phép tick Finnhub đến trễ |
| Output mode | append |
| Checkpoint | `s3a://checkpoints/trades-ohlcv-stream/` |
| Driver resources | 1 core / 512Mi |
| Executor | 1 instance / 1 core / 1g |

---

## Catalog config (dùng chung cả 2 app)

```scala
// Iceberg REST catalog qua Gravitino
spark.conf.set("spark.sql.catalog.gravitino_catalog",
               "org.apache.iceberg.spark.SparkCatalog")
spark.conf.set("spark.sql.catalog.gravitino_catalog.type", "rest")
spark.conf.set("spark.sql.catalog.gravitino_catalog.uri",
               sys.env("GRAVITINO_URI"))
spark.conf.set("spark.sql.catalog.gravitino_catalog.warehouse",
               sys.env("ICEBERG_WAREHOUSE"))

// MinIO (S3-compatible)
spark.conf.set("spark.hadoop.fs.s3a.endpoint",       sys.env("MINIO_ENDPOINT"))
spark.conf.set("spark.hadoop.fs.s3a.access.key",     sys.env("MINIO_ACCESS_KEY"))
spark.conf.set("spark.hadoop.fs.s3a.secret.key",     sys.env("MINIO_SECRET_KEY"))
spark.conf.set("spark.hadoop.fs.s3a.path.style.access", "true")
spark.conf.set("spark.hadoop.fs.s3a.impl",
               "org.apache.hadoop.fs.s3a.S3AFileSystem")
```

---

## Build & Deploy

### Bước 1: Build fat-jar

```bash
cd spark-application/news-ingest-stream
sbt assembly
# Output: target/scala-2.12/news-ingest-stream-assembly-1.0.0.jar

cd spark-application/trades-ohlcv-stream
sbt assembly
# Output: target/scala-2.12/trades-ohlcv-stream-assembly-1.0.0.jar
```

### Bước 2: Build và push Docker image

```bash
# App 1
cd spark-application/news-ingest-stream
docker build -t <registry>/news-ingest-stream:latest .
docker push <registry>/news-ingest-stream:latest

# App 2
cd spark-application/trades-ohlcv-stream
docker build -t <registry>/trades-ohlcv-stream:latest .
docker push <registry>/trades-ohlcv-stream:latest
```

### Bước 3: Deploy lên Kubernetes

```bash
# App 1
kubectl apply -f spark-application/news-ingest-stream/k8s/spark-application.yaml

# App 2
kubectl apply -f spark-application/trades-ohlcv-stream/k8s/spark-application.yaml
```

### Monitoring

```bash
# Xem trạng thái job
kubectl get sparkapplication -n stock-anomaly-detection

# Xem driver logs (App 1)
kubectl logs -n stock-anomaly-detection \
  -l spark-role=driver,spark-app-name=news-ingest-stream -f

# Xem driver logs (App 2)
kubectl logs -n stock-anomaly-detection \
  -l spark-role=driver,spark-app-name=trades-ohlcv-stream -f
```

### Restart / recovery

```bash
# Delete và apply lại để restart (checkpoint giữ nguyên offset)
kubectl delete sparkapplication news-ingest-stream -n stock-anomaly-detection
kubectl apply -f spark-application/news-ingest-stream/k8s/spark-application.yaml
```

---

## Vị trí source code

```
spark-application/
├── news-ingest-stream/
│   ├── build.sbt
│   ├── project/plugins.sbt
│   ├── src/main/scala/NewsIngestStream.scala
│   ├── Dockerfile
│   └── k8s/spark-application.yaml
└── trades-ohlcv-stream/
    ├── build.sbt
    ├── project/plugins.sbt
    ├── src/main/scala/TradesOhlcvStream.scala
    ├── Dockerfile
    └── k8s/spark-application.yaml
```
