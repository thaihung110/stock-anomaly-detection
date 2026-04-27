# ── Stage 1: Compile ──────────────────────────────────────────────────────────
# eclipse-temurin:17.0.16_8-jdk-noble: 0 critical / 0 high (sliplane scan)
# This stage is build-only and never ships to production.
FROM eclipse-temurin:17.0.16_8-jdk-noble AS builder

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fL "https://github.com/sbt/sbt/releases/download/v1.9.9/sbt-1.9.9.tgz" | tar xz -C /usr/local && \
    ln -s /usr/local/sbt/bin/sbt /usr/local/bin/sbt && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependency layer — cached until build.sbt or project/ changes
COPY build.sbt .
COPY project/ project/
RUN sbt update

# Build layer — only re-runs when src/ changes
COPY src/ src/
RUN sbt assembly

# ── Stage 2: Spark file source (intermediate, never ships) ────────────────────
# Used only to extract the Spark runtime and entrypoint into the clean final image.
FROM apache/spark:3.5.3-scala2.12-java17-ubuntu AS spark-files

# ── Stage 3: Final runtime — Amazon Corretto 17 on Alpine 3.21 ───────────────
# amazoncorretto:17-alpine3.21: Amazon backports security patches aggressively
FROM amazoncorretto:17-alpine3.21

RUN apk add --no-cache bash tini procps && \
    ln -s /sbin/tini /usr/bin/tini && \
    addgroup -S -g 185 spark && \
    adduser -S -u 185 -G spark -H -s /bin/bash spark

COPY --from=spark-files /opt/spark /opt/spark
COPY --from=spark-files /opt/entrypoint.sh /opt/entrypoint.sh

RUN chmod +x /opt/entrypoint.sh && \
    mkdir -p /opt/spark/work-dir && \
    chmod g+w /opt/spark/work-dir && \
    chown -R spark:spark /opt/spark

ENV SPARK_HOME=/opt/spark
ENV PATH="${SPARK_HOME}/bin:${SPARK_HOME}/sbin:${PATH}"

COPY --from=builder /build/target/scala-2.12/ohlcv-daily-loader-assembly-1.0.0.jar \
     /opt/spark/jars/ohlcv-daily-loader-assembly-1.0.0.jar
# Default symbols file — overridden by ConfigMap mount at /opt/spark/conf/symbols.txt
COPY conf/symbols.txt /opt/spark/conf/symbols.txt

WORKDIR /opt/spark/work-dir
USER spark
ENTRYPOINT ["/opt/entrypoint.sh"]
