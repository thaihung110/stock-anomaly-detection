#!/bin/bash

# Script to create Kafka topics with SASL authentication
# Usage: ./create_kafka_topics.sh

set -e

NAMESPACE="${NAMESPACE:-default}"
KAFKA_POD="openhouse-kafka-controller-0"
TOPIC_NAME="${1:-market-data.finnhub.crypto-trades.bronze}"
PARTITIONS="${2:-1}"
REPLICATION_FACTOR="${3:-1}"
SASL_USERNAME="${SASL_USERNAME:-admin}"
SASL_PASSWORD="${SASL_PASSWORD:-admin}"

echo "=========================================="
echo "Creating Kafka Topic (SASL)"
echo "=========================================="
echo "Topic Name: $TOPIC_NAME"
echo "Namespace: $NAMESPACE"
echo "Partitions: $PARTITIONS"
echo "Replication Factor: $REPLICATION_FACTOR"
echo "SASL Username: $SASL_USERNAME"
echo "=========================================="
echo ""

# Create a temporary client properties file with SASL configuration
echo "Creating temporary SASL configuration..."
kubectl exec -n $NAMESPACE $KAFKA_POD -- bash -c "cat > /tmp/client.properties <<EOF
security.protocol=SASL_PLAINTEXT
sasl.mechanism=PLAIN
sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username=\"${SASL_USERNAME}\" password=\"${SASL_PASSWORD}\";
EOF"

# Create the topic using kafka-topics.sh from within the Kafka pod
echo "Creating topic..."
kubectl exec -n $NAMESPACE $KAFKA_POD -- kafka-topics.sh \
  --create \
  --topic $TOPIC_NAME \
  --partitions $PARTITIONS \
  --replication-factor $REPLICATION_FACTOR \
  --if-not-exists \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/client.properties

echo ""
echo "✅ Topic '$TOPIC_NAME' created successfully!"

# List all topics to verify
echo ""
echo "=========================================="
echo "Current Topics:"
echo "=========================================="
kubectl exec -n $NAMESPACE $KAFKA_POD -- kafka-topics.sh \
  --list \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/client.properties

# Describe the topic
echo ""
echo "=========================================="
echo "Topic Details:"
echo "=========================================="
kubectl exec -n $NAMESPACE $KAFKA_POD -- kafka-topics.sh \
  --describe \
  --topic $TOPIC_NAME \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/client.properties

# Cleanup temporary file
echo ""
echo "Cleaning up temporary files..."
kubectl exec -n $NAMESPACE $KAFKA_POD -- rm -f /tmp/client.properties
