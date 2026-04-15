#!/bin/bash

# Script to create Kafka topics with SASL authentication
# Usage: ./create_kafka_topics.sh

set -e

NAMESPACE="${NAMESPACE:-default}"
KAFKA_POD="openhouse-kafka-controller-0"
TOPIC_NAME="market-data.finnhub.crypto-trades.bronze"
PARTITIONS=3
REPLICATION_FACTOR=1
SASL_USERNAME="admin"
SASL_PASSWORD="admin"

echo "Creating Kafka topic: $TOPIC_NAME"
echo "Namespace: $NAMESPACE"
echo "Partitions: $PARTITIONS"
echo "Replication Factor: $REPLICATION_FACTOR"

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

echo "Topic '$TOPIC_NAME' created successfully!"

# List all topics to verify
echo ""
echo "Current topics:"
kubectl exec -n $NAMESPACE $KAFKA_POD -- kafka-topics.sh \
  --list \
  --bootstrap-server localhost:9092 \
  --command-config /tmp/client.properties

# Cleanup temporary file
kubectl exec -n $NAMESPACE $KAFKA_POD -- rm -f /tmp/client.properties
