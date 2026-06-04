package com.stockanomalydetection.newsingest.pipeline

import com.stockanomalydetection.newsingest.config.AppConfig
import com.stockanomalydetection.newsingest.schema.NewsSchema
import org.apache.spark.sql.functions.{col, from_json, from_unixtime, to_date, to_timestamp, trim}
import org.apache.spark.sql.streaming.{StreamingQuery, Trigger}
import org.apache.spark.sql.types.StringType
import org.apache.spark.sql.{DataFrame, SparkSession}

object NewsPipeline {
  def buildRawStream(spark: SparkSession, config: AppConfig): DataFrame =
    spark.readStream
      .format("kafka")
      .option("kafka.bootstrap.servers", config.kafkaBootstrapServers)
      .option("subscribe", config.inputTopic)
      .option("startingOffsets", "earliest")
      .option("failOnDataLoss", "false")
      .option("maxOffsetsPerTrigger", config.maxOffsetsPerTrigger)
      .load()

  // Kafka contract (finnhub-news-producer/schema.py):
  //   article_id, symbol, headline, summary, url, source, category, published_at_ms, fetched_at_ms
  // Bronze table canonical names:
  //   article_id, symbol, title, description, url, source_name, published_at, fetched_at, published_date
  def transform(rawStream: DataFrame): DataFrame =
    rawStream
      .select(from_json(col("value").cast(StringType), NewsSchema.schema).as("d"))
      .select("d.*")
      // Require url and headline — rows without either are unprocessable
      .filter(
        col("url").isNotNull      && trim(col("url"))      =!= "" &&
        col("headline").isNotNull && trim(col("headline")) =!= ""
      )
      // Remap producer field names → canonical bronze column names
      .withColumnRenamed("headline", "title")
      .withColumnRenamed("summary",  "description")
      .withColumnRenamed("source",   "source_name")
      // Producer stores epoch milliseconds; divide by 1000 to get seconds for from_unixtime
      .withColumn("published_at",   to_timestamp(from_unixtime(col("published_at_ms").cast("long") / 1000)))
      .withColumn("fetched_at",     to_timestamp(from_unixtime(col("fetched_at_ms").cast("long")   / 1000)))
      .withColumn("published_date", to_date(col("published_at")))
      .withWatermark("published_at", "10 minutes")
      .dropDuplicates(Seq("article_id"))
      .select(
        col("article_id"),
        col("symbol"),
        col("source_name"),
        col("title"),
        col("description"),
        col("url"),
        col("category"),
        col("published_at"),
        col("fetched_at"),
        col("published_date")
      )

  def write(transformed: DataFrame, config: AppConfig): StreamingQuery =
    transformed.writeStream
      .format("iceberg")
      .option("path", config.outputTable)
      .option("checkpointLocation", config.checkpointLocation)
      .option("distribution-mode", config.writeDistributionMode)
      .option("target-file-size-bytes", config.targetFileSizeBytes)
      .option("fanout-enabled", config.fanoutEnabled)
      .outputMode("append")
      .trigger(Trigger.ProcessingTime(config.triggerInterval))
      .start()
}
