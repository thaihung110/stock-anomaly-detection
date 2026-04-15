# Dockerfile for Spark History Server
# Dùng cùng base image và cùng version JARs với các Spark jobs trong project
# để đảm bảo compatibility.
#
# Điểm khác biệt so với job Dockerfiles:
#   - KHÔNG có Iceberg JARs (History Server không đọc/ghi Iceberg)
#   - KHÔNG có Kafka JARs (History Server không kết nối Kafka)
#   - KHÔNG có application code (Python scripts)
#   - CHỈ cần hadoop-aws để đọc event logs từ MinIO qua S3A protocol
#
# Build: cd spark-jobs/spark-history-server && ./build-image.sh
# Image: hungvt0110/spark-history-server:latest
FROM apache/spark:3.5.0-python3

USER root

# ==============================================================================
# Pre-download Hadoop AWS JARs để History Server đọc được S3A (MinIO)
# Cùng versions với load-crypto-bronze để tránh xung đột classpath
# ==============================================================================
ARG SPARK_VERSION=3.5.0
ARG SCALA_VERSION=2.12
ARG HADOOP_VERSION=3.3.4

# Chỉ cần hadoop-aws và aws-java-sdk-bundle để kết nối S3A/MinIO
# Không cần thêm gì khác - History Server chỉ đọc .lz4/.zstd event log files
ARG PACKAGES="org.apache.hadoop:hadoop-aws:${HADOOP_VERSION}"

# Download JARs qua spark-submit --packages (Ivy resolver),
# sau đó copy vào /opt/spark/jars/ để luôn available mà không cần --packages lúc runtime
RUN echo "print('Downloading hadoop-aws JARs...')" > /tmp/download.py && \
    /opt/spark/bin/spark-submit --packages $PACKAGES /tmp/download.py && \
    find /root/.ivy2 -type f -name "*.jar" -exec cp {} /opt/spark/jars/ \; && \
    rm -rf /root/.ivy2 /tmp/download.py

# ==============================================================================
# Permissions
# ==============================================================================
# uid 185 = default spark user trong apache/spark image
RUN chmod 777 /opt/spark/logs 2>/dev/null || mkdir -p /opt/spark/logs && chmod 777 /opt/spark/logs

# Switch về spark user (uid 185) - không chạy root trong production
USER 185

# ==============================================================================
# History Server chạy qua SPARK_HISTORY_OPTS environment variable
# Không có CMD mặc định - được set trong K8s Deployment command field
# ==============================================================================
