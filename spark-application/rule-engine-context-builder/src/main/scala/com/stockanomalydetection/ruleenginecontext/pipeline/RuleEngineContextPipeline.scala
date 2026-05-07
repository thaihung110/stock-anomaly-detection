package com.stockanomalydetection.ruleenginecontext.pipeline

import com.stockanomalydetection.ruleenginecontext.config.AppConfig
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types.{DateType, DoubleType}
import org.apache.spark.sql.{DataFrame, SparkSession}

import java.sql.Date
import java.time.{LocalDate, ZoneOffset}
import java.time.format.DateTimeFormatter

object RuleEngineContextPipeline {
  private val logger = LogManager.getLogger(getClass)

  private val ymdKeyFmt: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyyMMdd")

  /** UTC calendar yesterday as YYYYMMDD int (dim_date / fact date_key convention). */
  def defaultAsOfDateKey(): Int = {
    val d = LocalDate.now(ZoneOffset.UTC).minusDays(1)
    ymdKeyFmt.format(d).toInt
  }

  def dateKeyToSqlDate(dateKey: Int): Date = {
    val keyStr = f"$dateKey%08d"
    Date.valueOf(LocalDate.parse(keyStr, ymdKeyFmt))
  }

  def readFact(spark: SparkSession, inputTable: String): DataFrame = {
    logger.info(s"Reading fact table (full history for rolling windows): $inputTable")
    spark.table(inputTable)
  }

  def readDimSymbol(spark: SparkSession, dimSymbolTable: String): DataFrame = {
    logger.info(s"Reading dim_symbol (active): $dimSymbolTable")
    spark.table(dimSymbolTable)
      .filter(col("is_active") === true)
      .select(col("symbol_key"), col("symbol"))
  }

  def withSymbolAndRolling(
    fact:    DataFrame,
    dimSym:  DataFrame,
    dateKey: Int
  ): DataFrame = {
    val joined = fact.join(broadcast(dimSym), Seq("symbol_key"), "inner")

    val w    = Window.partitionBy("symbol_key").orderBy("date_key")
    val w5   = w.rowsBetween(-4, 0)

    val withRolling = joined
      .withColumn("mean_return_5d", avg(col("daily_return")).over(w5))
      .withColumn("std_return_5d", stddev_pop(col("daily_return")).over(w5))
      .withColumn("mean_volume_5d", avg(col("volume").cast(DoubleType)).over(w5))
      .withColumn("vwap_5d_avg", avg(col("vwap")).over(w5))

    logger.info(s"Filtering to date_key=$dateKey (single trading day snapshot)")
    withRolling.filter(col("date_key") === lit(dateKey))
  }

  def toOutputColumns(df: DataFrame, asOfDateKey: Int): DataFrame = {
    val asOfDateLit = lit(dateKeyToSqlDate(asOfDateKey)).cast(DateType)
    df
      .withColumn("as_of_date", asOfDateLit)
      .select(
        col("symbol"),
        col("as_of_date"),
        col("mean_return_20d"),
        col("std_return_20d"),
        col("mean_return_5d"),
        col("std_return_5d"),
        col("mean_volume_20d"),
        col("std_volume_20d"),
        col("mean_volume_5d"),
        col("bb_upper").alias("bb_upper_20d"),
        col("bb_lower").alias("bb_lower_20d"),
        col("bb_mid").alias("bb_mid_20d"),
        col("atr_14"),
        col("rsi_14"),
        col("vwap_5d_avg"),
        current_timestamp().alias("updated_at")
      )
  }

  def overwritePartition(df: DataFrame, outputTable: String): Unit = {
    val cached   = df.cache()
    val rowCount = cached.count()
    logger.info(s"Overwriting partition in $outputTable — $rowCount rows")
    try {
      cached.writeTo(outputTable).overwritePartitions()
    } catch {
      case e: Exception =>
        cached.unpersist()
        throw new RuntimeException(s"overwritePartitions to $outputTable failed: ${e.getMessage}", e)
    }
    cached.unpersist()
    logger.info(s"Partition write complete — $rowCount rows")
  }

  def run(spark: SparkSession, cfg: AppConfig): Unit = {
    val dateKey = cfg.asOfDateKey.getOrElse(defaultAsOfDateKey())
    logger.info(s"Building rule_engine_context for as_of_date_key=$dateKey (UTC)")

    val fact   = readFact(spark, cfg.inputTable)
    val dimSym = readDimSymbol(spark, cfg.dimSymbolTable)

    val sliced = withSymbolAndRolling(fact, dimSym, dateKey)
    val out    = toOutputColumns(sliced, dateKey)

    overwritePartition(out, cfg.outputTable)
  }
}
