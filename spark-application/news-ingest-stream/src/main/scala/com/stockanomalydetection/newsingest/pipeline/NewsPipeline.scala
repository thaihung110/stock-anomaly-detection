package com.stockanomalydetection.newsingest.pipeline

import com.stockanomalydetection.newsingest.config.AppConfig
import com.stockanomalydetection.newsingest.schema.NewsSchema
import org.apache.spark.sql.functions.{col, from_json, md5, to_timestamp}
import org.apache.spark.sql.streaming.{StreamingQuery, Trigger}
import org.apache.spark.sql.types.StringType
import org.apache.spark.sql.{DataFrame, SparkSession}

object NewsPipeline {
  def buildRawStream(spark: SparkSession, config: AppConfig): DataFrame =
    spark.readStream
      .format("kafka")
      .option("kafka.bootstrap.servers", config.kafkaBootstrapServers)
      .option("subscribe", config.inputTopic)
      .option("startingOffsets", "latest")
      .option("failOnDataLoss", "false")
      .load()

  def transform(rawStream: DataFrame): DataFrame =
    rawStream
      .select(from_json(col("value").cast(StringType), NewsSchema.schema).as("d"))
      .select("d.*")
      .withColumn("article_id", md5(col("url")))
      .withColumn("published_at", to_timestamp(col("published_at")))
      .withColumn("fetched_at", to_timestamp(col("fetched_at")))
      .withWatermark("published_at", "10 minutes")
      .dropDuplicates(Seq("article_id"))
      .select(
        col("article_id"),
        col("symbol"),
        col("source_name"),
        col("author"),
        col("title"),
        col("description"),
        col("url"),
        col("published_at"),
        col("content"),
        col("search_query"),
        col("fetched_at")
      )

  def write(transformed: DataFrame, config: AppConfig): StreamingQuery =
    transformed.writeStream
      .format("iceberg")
      .option("path", config.outputTable)
      .option("checkpointLocation", config.checkpointLocation)
      .outputMode("append")
      .trigger(Trigger.ProcessingTime(config.triggerInterval))
      .start()
}
