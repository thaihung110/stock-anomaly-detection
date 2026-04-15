# Data Ingestion Infrastructure

Kubernetes resources for real-time crypto trading data ingestion using FinnhubProducer.

## 📁 Directory Structure

```
ingestion/
├── application/
│   └── finnhub-producer.yaml          # FinnhubProducer deployment
│
└── scripts/
    ├── deploy_finnhub_producer.sh     # Deploy producer
    └── undeploy_finnhub_producer.sh   # Remove producer
```

---

## 🚀 FinnhubProducer

**Purpose**: Real-time crypto trade data ingestion from Finnhub WebSocket API to Kafka

**Workflow**:

```
Finnhub WebSocket API
    ↓ (Real-time trades)
FinnhubProducer (Python)
    ↓ (Avro encoding)
Kafka Topic (market-data.finnhub.crypto-trades.bronze)
```

**Key Features**:

- ✅ WebSocket connection to Finnhub API
- ✅ Real-time crypto trade streaming
- ✅ Avro message encoding
- ✅ Kafka producer with SASL authentication
- ✅ Configurable via environment variables

---

## 📋 Configuration

### Manifest: `application/finnhub-producer.yaml`

**Secret** (`finnhub-api-secret`):

```yaml
data:
  FINNHUB_API_TOKEN: <base64-encoded-token>
  KAFKA_SASL_USERNAME: YWRtaW4= # admin
  KAFKA_SASL_PASSWORD: YWRtaW4= # admin
```

**ConfigMap** (`finnhub-producer-config`):

```yaml
data:
  KAFKA_SERVER: "openhouse-kafka"
  KAFKA_PORT: "9092"
  KAFKA_TOPIC_NAME: "market-data.finnhub.crypto-trades.bronze"
  FINNHUB_STOCKS_TICKERS: "BINANCE:BTCUSDT,BINANCE:ETHUSDT"
  FINNHUB_VALIDATE_TICKERS: "false"
```

**Deployment**:

```yaml
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: finnhub-producer
          image: hungvt0110/finnhub-producer:latest
          env:
            - name: FINNHUB_API_TOKEN
              valueFrom:
                secretKeyRef:
                  name: finnhub-api-secret
                  key: FINNHUB_API_TOKEN
            # ... other env vars from ConfigMap and Secret
```

---

## 🚀 Deployment

### Deploy FinnhubProducer

```bash
cd scripts

# Deploy
./deploy_finnhub_producer.sh

# Verify
kubectl get deployment finnhub-producer
kubectl get pods -l app=finnhub-producer
```

### Check Logs

```bash
# View producer logs
kubectl logs -l app=finnhub-producer -f

# Expected output:
# Connected to Finnhub WebSocket
# Subscribed to BINANCE:BTCUSDT
# Message sent to Kafka: {...}
```

### Undeploy

```bash
cd scripts

# Remove producer
./undeploy_finnhub_producer.sh
```

---

## 🔧 Configuration Updates

### Change Crypto Symbols

Edit `application/finnhub-producer.yaml`:

```yaml
data:
  FINNHUB_STOCKS_TICKERS: "BINANCE:BTCUSDT,BINANCE:ETHUSDT,BINANCE:SOLUSDT"
```

Then redeploy:

```bash
kubectl apply -f application/finnhub-producer.yaml
kubectl rollout restart deployment finnhub-producer
```

### Update Finnhub API Token

```bash
# Create new secret
kubectl create secret generic finnhub-api-secret \
  --from-literal=FINNHUB_API_TOKEN='your-new-token' \
  --from-literal=KAFKA_SASL_USERNAME='admin' \
  --from-literal=KAFKA_SASL_PASSWORD='admin' \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart deployment
kubectl rollout restart deployment finnhub-producer
```

### Change Kafka Topic

Edit ConfigMap in `application/finnhub-producer.yaml`:

```yaml
data:
  KAFKA_TOPIC_NAME: "new-topic-name"
```

Apply and restart:

```bash
kubectl apply -f application/finnhub-producer.yaml
kubectl rollout restart deployment finnhub-producer
```

---

## 📊 Monitoring

### Check Producer Status

```bash
# Deployment status
kubectl get deployment finnhub-producer

# Pod status
kubectl get pods -l app=finnhub-producer

# Describe pod for events
kubectl describe pod -l app=finnhub-producer
```

### Verify Kafka Messages

```bash
# Run Kafka consumer to check messages
kubectl run kafka-consumer --rm -it \
  --image=confluentinc/cp-kafka:latest \
  -- kafka-console-consumer \
  --bootstrap-server openhouse-kafka:9092 \
  --topic market-data.finnhub.crypto-trades.bronze \
  --from-beginning \
  --max-messages 10 \
  --consumer-property security.protocol=SASL_PLAINTEXT \
  --consumer-property sasl.mechanism=PLAIN \
  --consumer-property sasl.jaas.config='org.apache.kafka.common.security.plain.PlainLoginModule required username="admin" password="admin";'
```

---

## 🛠️ Troubleshooting

### Producer Not Starting

**Check logs**:

```bash
kubectl logs -l app=finnhub-producer --tail=50
```

**Common issues**:

- Invalid Finnhub API token
- Kafka connection refused
- SASL authentication failed

### WebSocket Connection Errors

**Error**: `WebSocket connection failed`

**Solutions**:

- Verify Finnhub API token is valid
- Check network connectivity
- Ensure API rate limits not exceeded

### Kafka Authentication Errors

**Error**: `SASL authentication failed`

**Solutions**:

```bash
# Verify Kafka credentials
kubectl get secret finnhub-api-secret -o yaml

# Check Kafka is running with SASL
kubectl get pods -l app=kafka
kubectl logs -l app=kafka | grep SASL
```

### No Messages in Kafka

**Check**:

1. Producer logs show "Message sent to Kafka"
2. Kafka topic exists:
   ```bash
   kubectl exec -it kafka-0 -- kafka-topics.sh \
     --bootstrap-server localhost:9092 \
     --list \
     --command-config /tmp/client.properties
   ```
3. Consumer can connect to topic (see Monitoring section)

---

## 🔄 Data Flow

```
┌──────────────────┐
│  Finnhub API     │
│  (WebSocket)     │
└────────┬─────────┘
         │ Real-time trades
         │ (JSON)
         ▼
┌──────────────────┐
│ FinnhubProducer  │
│  (Python Pod)    │
│                  │
│  • Subscribe     │
│  • Parse JSON    │
│  • Encode Avro   │
│  • Send to Kafka │
└────────┬─────────┘
         │ Avro messages
         ▼
┌──────────────────┐
│  Kafka Topic     │
│  (Bronze)        │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Spark Streaming  │
│ (load-crypto-    │
│  bronze)         │
└──────────────────┘
```

---

## 📚 References

- [FinnhubProducer Source Code](../../../FinnhubProducer/README.md)
- [Finnhub API Documentation](https://finnhub.io/docs/api/websocket-trades)
- [Kafka Documentation](https://kafka.apache.org/documentation/)
- [Spark Bronze Job](../compute/applications/spark/bronze-layer/jobs/load-crypto-bronze.yaml)
