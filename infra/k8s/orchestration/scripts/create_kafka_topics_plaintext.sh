#!/bin/bash

# Script to create Kafka topics (PLAINTEXT - No SASL)
# Usage:
#   Create full V3.3 topic set:
#     ./create_kafka_topics_plaintext.sh
#
#   Create single topic:
#     ./create_kafka_topics_plaintext.sh <topic_name> [partitions] [replication_factor]
#
# Env vars:
#   NAMESPACE                (default: stock-anomaly-detection)
#   KAFKA_POD                (default: openhouse-kafka-controller-0)
#   BOOTSTRAP_SERVER         (default: localhost:9092)
#   TOPIC_PARTITIONS_DEFAULT (default: 3)
#   REPLICATION_FACTOR       (default: 1)
#   RETENTION_MS_DEFAULT     (default: 604800000 = 7 days)

set -euo pipefail

NAMESPACE="${NAMESPACE:-stock-anomaly-detection}"
KAFKA_POD="${KAFKA_POD:-openhouse-kafka-controller-0}"
BOOTSTRAP_SERVER="${BOOTSTRAP_SERVER:-localhost:9092}"
TOPIC_PARTITIONS_DEFAULT="${TOPIC_PARTITIONS_DEFAULT:-3}"
REPLICATION_FACTOR_DEFAULT="${REPLICATION_FACTOR:-1}"
RETENTION_MS_DEFAULT="${RETENTION_MS_DEFAULT:-604800000}"

# Topics V3.3 — tất cả services trong namespace stock-anomaly-detection:
#
# Producer → Topic → Consumer
# yfinance-quotes-producer   → raw.stock.quotes  → Rule Engine
# finnhub-trades-producer    → raw.stock.trades  → trades-ohlcv-stream (Spark)
# finnhub-news-producer      → raw.stock.news    → news-ingest-stream (Spark)
# Rule Engine (system rules) → alerts.raw        → Alert Service  (ADR-002: nối thẳng, bypass LLM)
# Rule Engine (custom rules) → alerts.user       → Alert Service  (ADR-001: SoC, custom delivery)
# Alert Service (DLQ)        → alerts.failed     → Operator replay tooling
# LLM Agent (future)         → alerts.confirmed  → Alert Service (future — khi LLM Agent sẵn sàng)
V33_TOPICS=(
  "raw.stock.quotes"
  "raw.stock.trades"
  "raw.stock.news"
  "alerts.raw"
  "alerts.user"
  "alerts.failed"
  "alerts.confirmed"
)

create_topic() {
  local topic_name="$1"
  local partitions="$2"
  local replication_factor="$3"
  local retention_ms="$4"

  echo "Creating topic '${topic_name}' (partitions=${partitions}, rf=${replication_factor}, retention_ms=${retention_ms})"
  kubectl exec -n "${NAMESPACE}" "${KAFKA_POD}" -- kafka-topics.sh \
    --create \
    --if-not-exists \
    --topic "${topic_name}" \
    --partitions "${partitions}" \
    --replication-factor "${replication_factor}" \
    --config "retention.ms=${retention_ms}" \
    --bootstrap-server "${BOOTSTRAP_SERVER}"
}

describe_topic() {
  local topic_name="$1"
  kubectl exec -n "${NAMESPACE}" "${KAFKA_POD}" -- kafka-topics.sh \
    --describe \
    --topic "${topic_name}" \
    --bootstrap-server "${BOOTSTRAP_SERVER}"
}

echo "=========================================="
echo "Kafka topic bootstrap (PLAINTEXT)"
echo "=========================================="
echo "Namespace:          ${NAMESPACE}"
echo "Kafka pod:          ${KAFKA_POD}"
echo "Bootstrap server:   ${BOOTSTRAP_SERVER}"
echo "Default partitions: ${TOPIC_PARTITIONS_DEFAULT}"
echo "Default RF:         ${REPLICATION_FACTOR_DEFAULT}"
echo "Default retention:  ${RETENTION_MS_DEFAULT} ms"
echo "=========================================="
echo ""

if [[ $# -ge 1 ]]; then
  # Single-topic mode for ad-hoc topic creation
  TOPIC_NAME="$1"
  PARTITIONS="${2:-${TOPIC_PARTITIONS_DEFAULT}}"
  REPLICATION_FACTOR_SINGLE="${3:-${REPLICATION_FACTOR_DEFAULT}}"
  create_topic "${TOPIC_NAME}" "${PARTITIONS}" "${REPLICATION_FACTOR_SINGLE}" "${RETENTION_MS_DEFAULT}"
  echo ""
  echo "✅ Topic '${TOPIC_NAME}' created/updated."
  echo ""
  echo "Topic details:"
  describe_topic "${TOPIC_NAME}"
else
  # Plan V3.3 default topics
  for topic in "${V33_TOPICS[@]}"; do
    create_topic "${topic}" "${TOPIC_PARTITIONS_DEFAULT}" "${REPLICATION_FACTOR_DEFAULT}" "${RETENTION_MS_DEFAULT}"
  done

  echo ""
  echo "✅ All V3.3 topics created/verified."
  echo ""
  echo "=========================================="
  echo "Current topics:"
  echo "=========================================="
  kubectl exec -n "${NAMESPACE}" "${KAFKA_POD}" -- kafka-topics.sh \
    --list \
    --bootstrap-server "${BOOTSTRAP_SERVER}"

  echo ""
  echo "=========================================="
  echo "V3.3 topic details:"
  echo "=========================================="
  for topic in "${V33_TOPICS[@]}"; do
    describe_topic "${topic}"
  done
fi
