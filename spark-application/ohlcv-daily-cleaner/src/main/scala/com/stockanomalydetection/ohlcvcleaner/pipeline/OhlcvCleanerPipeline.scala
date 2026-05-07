package com.stockanomalydetection.ohlcvcleaner.pipeline

import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions.{coalesce, col, current_timestamp, lit}

object OhlcvCleanerPipeline {
  private val logger = LogManager.getLogger(getClass)

  def readBronze(spark: SparkSession, inputTable: String): DataFrame = {
    logger.info(s"Reading bronze table: $inputTable")
    spark.table(inputTable)
  }

  def transform(df: DataFrame): DataFrame = {
    val totalBefore = df.count()
    logger.info(s"Bronze rows before cleaning: $totalBefore")

    val cleaned = df
      // Drop rows where any OHLCV field is null or zero — these are unusable for analytics
      .filter(
        col("open").isNotNull   && col("open")   > 0 &&
        col("high").isNotNull   && col("high")   > 0 &&
        col("low").isNotNull    && col("low")    > 0 &&
        col("close").isNotNull  && col("close")  > 0 &&
        col("volume").isNotNull && col("volume") > 0
      )
      // is_complete = true only when adj_close was present in source; false means we fell back to close
      .withColumn("is_complete", col("adj_close").isNotNull)
      // Fall back to raw close when adj_close is absent (rare for recent data)
      .withColumn("adj_close", coalesce(col("adj_close"), col("close")))
      // VWAP proxy — used downstream by rule engine context builder
      .withColumn(
        "vwap_estimate",
        (col("open") + col("high") + col("low") + col("close")) / 4.0
      )
      .withColumn("data_source", lit("yfinance"))
      .withColumn("cleaned_at",  current_timestamp())
      .select(
        col("symbol"),
        col("trade_date"),
        col("open"),
        col("high"),
        col("low"),
        col("close"),
        col("adj_close"),
        col("volume"),
        col("dividends"),
        col("stock_splits"),
        col("vwap_estimate"),
        col("data_source"),
        col("is_complete"),
        col("cleaned_at")
      )

    val totalAfter = cleaned.count()
    logger.info(s"Rows after cleaning: $totalAfter (dropped ${totalBefore - totalAfter} incomplete rows)")
    cleaned
  }

  def mergeInto(spark: SparkSession, df: DataFrame, outputTable: String): Unit = {
    val cached = df.cache()
    val rowCount = cached.count()
    cached.createOrReplaceTempView("incoming_ohlcv_clean")
    logger.info(s"Merging $rowCount rows into $outputTable")

    try {
      spark.sql(s"""
        MERGE INTO $outputTable AS t
        USING (SELECT * FROM incoming_ohlcv_clean) AS s
        ON t.symbol = s.symbol AND t.trade_date = s.trade_date
        WHEN MATCHED THEN UPDATE SET
          t.open          = s.open,
          t.high          = s.high,
          t.low           = s.low,
          t.close         = s.close,
          t.adj_close     = s.adj_close,
          t.volume        = s.volume,
          t.dividends     = s.dividends,
          t.stock_splits  = s.stock_splits,
          t.vwap_estimate = s.vwap_estimate,
          t.data_source   = s.data_source,
          t.is_complete   = s.is_complete,
          t.cleaned_at    = s.cleaned_at
        WHEN NOT MATCHED THEN INSERT *
      """)
    } catch {
      case e: Exception =>
        cached.unpersist()
        throw new RuntimeException(s"MERGE INTO $outputTable failed: ${e.getMessage}", e)
    }

    cached.unpersist()
    logger.info(s"Merge complete — $rowCount rows upserted into $outputTable")
  }
}
