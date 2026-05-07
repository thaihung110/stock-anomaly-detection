package com.stockanomalydetection.newscleaner.pipeline

import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions.{col, current_timestamp, length, lit, lower, md5, rank, to_date, trim}

object NewsCleanerPipeline {
  private val logger = LogManager.getLogger(getClass)

  def readBronze(spark: SparkSession, inputTable: String): DataFrame = {
    logger.info(s"Reading bronze table: $inputTable")
    spark.table(inputTable)
  }

  def transform(df: DataFrame): DataFrame = {
    val totalBefore = df.count()
    logger.info(s"Bronze rows before cleaning: $totalBefore")

    // Window for headline-level dedup: within each dedup_hash group, keep the row
    // that arrived earliest (lowest fetched_at). Ties broken by article_id for stability.
    val dedupWindow = Window
      .partitionBy("dedup_hash")
      .orderBy(col("fetched_at").asc_nulls_last, col("article_id").asc)

    val cleaned = df
      // Rows without title or url are unprocessable — filter before any dedup work
      .filter(
        col("title").isNotNull && length(trim(col("title"))) > 0 &&
        col("url").isNotNull   && length(trim(col("url")))   > 0
      )
      // Compute headline dedup hash — repeated polls produce identical headlines for the same event
      .withColumn("dedup_hash",   md5(trim(col("title"))))
      // Normalise publisher name — downstream queries group by source_name
      .withColumn("source_name",  trim(lower(col("source_name"))))
      // Rank within each dedup group; rank=1 is the earliest-fetched occurrence
      .withColumn("_dedup_rank",  rank().over(dedupWindow))
      .filter(col("_dedup_rank") === 1)
      .drop("_dedup_rank")
      .withColumn("data_source",    lit("finnhub"))
      .withColumn("cleaned_at",     current_timestamp())
      // Recompute published_date from published_at — bronze may have stale/missing partition column
      .withColumn("published_date", to_date(col("published_at")))
      .select(
        col("article_id"),
        col("dedup_hash"),
        col("symbol"),
        col("source_name"),
        col("title"),
        col("description"),
        col("url"),
        col("category"),
        col("published_at"),
        col("fetched_at"),
        col("data_source"),
        col("cleaned_at"),
        col("published_date")
      )

    val totalAfter = cleaned.count()
    logger.info(s"Rows after cleaning: $totalAfter (dropped ${totalBefore - totalAfter} duplicates/invalid rows)")
    cleaned
  }

  // Dynamic partition overwrite — each run cleanly replaces only the partitions it touches,
  // making the job idempotent: re-running on the same day produces the same silver output.
  def writeToSilver(spark: SparkSession, df: DataFrame, outputTable: String): Unit = {
    spark.conf.set("spark.sql.iceberg.dynamic-partition-overwrite.enabled", "true")

    val rowCount = df.count()
    logger.info(s"Writing $rowCount cleaned rows to $outputTable (dynamic partition overwrite)")

    df.writeTo(outputTable)
      .option("distribution-mode", "hash")
      .overwritePartitions()

    logger.info(s"Write complete — $rowCount rows written to $outputTable")
  }
}
