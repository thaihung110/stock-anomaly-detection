package com.stockanomalydetection.dimloader.pipeline

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types.{DateType, IntegerType}

object DimSymbolPipeline {

  private val scdFields = Seq(
    "company_name", "exchange", "sector", "industry",
    "country", "currency", "market_cap", "shares_outstanding",
    "beta", "week_52_high", "week_52_low"
  )

  def run(spark: SparkSession, inputTable: String, outputTable: String): Unit = {
    // Latest snapshot per symbol from bronze (pick most recent ingested_at)
    val incoming = spark.table(inputTable)
      .withColumn("_rn",
        row_number().over(
          Window.partitionBy("symbol").orderBy(col("ingested_at").desc)))
      .filter(col("_rn") === 1)
      .drop("_rn")
      .select((Seq("symbol") ++ scdFields).map(col): _*)
      .cache()

    val incomingCount = incoming.count()
    println(s"[DimSymbolPipeline] Incoming symbols: $incomingCount")

    val existing = spark.table(outputTable)
      .filter(col("is_active") === true)
      .select((Seq("symbol") ++ scdFields).map(c => col(c).as(s"ex_$c")): _*)

    val joined = incoming.join(existing, incoming("symbol") === existing("ex_symbol"), "left_outer")

    // Detect which symbols have changed SCD fields
    val changeExpr = scdFields
      .map(f => incoming(f) =!= existing(s"ex_$f"))
      .reduce(_ || _)

    val changed = joined
      .filter(col("ex_symbol").isNotNull && changeExpr)
      .select(incoming.columns.map(incoming(_)): _*)
      .cache()

    val isNew = joined
      .filter(col("ex_symbol").isNull)
      .select(incoming.columns.map(incoming(_)): _*)
      .cache()

    val changedCount = changed.count()
    val newCount     = isNew.count()
    val unchangedCount = incomingCount - changedCount - newCount
    println(s"[DimSymbolPipeline] New: $newCount  Changed: $changedCount  Unchanged: $unchangedCount")

    // Step 3a: close old records for changed symbols via MERGE
    if (changedCount > 0) {
      changed.select("symbol").createOrReplaceTempView("changed_symbols")
      spark.sql(s"""
        MERGE INTO $outputTable AS t
        USING changed_symbols AS s
          ON t.symbol = s.symbol AND t.is_active = true
        WHEN MATCHED THEN UPDATE SET
          t.is_active    = false,
          t.effective_to = date_sub(current_date(), 1)
      """)
      println(s"[DimSymbolPipeline] Closed $changedCount old SCD2 records")
    }

    // Step 3b: insert new rows for both new and changed symbols
    val toInsert = changed.union(isNew)
    val toInsertCount = changedCount + newCount

    if (toInsertCount > 0) {
      val maxKey = spark.sql(s"SELECT COALESCE(MAX(symbol_key), 0) FROM $outputTable")
        .collect()(0).getLong(0)

      val withKeys = toInsert
        .withColumn("symbol_key",
          (lit(maxKey) + row_number().over(Window.orderBy("symbol"))).cast(IntegerType))
        .withColumn("is_active",      lit(true))
        .withColumn("effective_from", current_date())
        .withColumn("effective_to",   lit(null).cast(DateType))
        .withColumn("source",         lit("finnhub"))

      withKeys.writeTo(outputTable).append()
      println(s"[DimSymbolPipeline] Inserted $toInsertCount new SCD2 records")
    }

    incoming.unpersist()
    changed.unpersist()
    isNew.unpersist()
  }
}
