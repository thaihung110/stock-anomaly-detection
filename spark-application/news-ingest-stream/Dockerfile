FROM apache/spark:3.5.1-scala2.12-java17-python3-ubuntu

USER root

# Copy fat-jar built by sbt assembly
COPY target/scala-2.12/news-ingest-stream-assembly-1.0.0.jar \
     /opt/spark/jars/news-ingest-stream-assembly-1.0.0.jar

# spark-operator manages ENTRYPOINT — no override needed
USER spark
