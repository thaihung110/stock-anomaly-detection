package com.stockanomalydetection.tradesohlcv.pipeline

import com.stockanomalydetection.tradesohlcv.config.AppConfig
import com.stockanomalydetection.tradesohlcv.schema.TradeSchema
import org.apache.spark.sql.functions.{col, count, from_json, max, max_by, min, min_by, sum, window}
import org.apache.spark.sql.streaming.{StreamingQuery, Trigger}
import org.apache.spark.sql.types.{DateType, DoubleType, IntegerType, StringType, TimestampType}
import org.apache.spark.sql.{DataFrame, SparkSession}

object TradesOhlcvPipeline {
  def buildRawStream(spark: SparkSession, config: AppConfig): DataFrame =
    spark.readStream
      .format("kafka")
      .option("kafka.bootstrap.servers", config.kafkaBootstrapServers)
      .option("subscribe", config.inputTopic)
      .option("startingOffsets", "earliest")
      .option("failOnDataLoss", "false")
      .option("maxOffsetsPerTrigger", config.maxOffsetsPerTrigger)
      .load()

  def transform(rawStream: DataFrame): DataFrame = {
    val parsed = rawStream
      .select(from_json(col("value").cast(StringType), TradeSchema.schema).as("d"))
      .select("d.*")
      .withColumn("bar_ts", (col("timestamp_ms") / 1000L).cast(TimestampType))
      .withWatermark("bar_ts", "5 minutes")

    parsed
      .groupBy(col("symbol"), window(col("bar_ts"), "1 minute"))
      .agg(
        min_by(col("price"), col("timestamp_ms")).as("open"),
        max("price").as("high"),
        min("price").as("low"),
        max_by(col("price"), col("timestamp_ms")).as("close"),
        sum("volume").as("volume"),
        count("*").cast(IntegerType).as("trade_count"),
        (sum(col("price") * col("volume").cast(DoubleType)) /
          sum(col("volume").cast(DoubleType))).as("vwap")
      )
      .select(
        col("window.start").as("bar_ts"),
        col("symbol"),
        col("open"),
        col("high"),
        col("low"),
        col("close"),
        col("volume"),
        col("trade_count"),
        col("vwap"),
        col("window.start").cast(DateType).as("bar_date")
      )
  }

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
