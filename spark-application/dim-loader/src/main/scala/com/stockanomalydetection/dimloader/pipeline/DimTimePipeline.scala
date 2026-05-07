package com.stockanomalydetection.dimloader.pipeline

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types.IntegerType

object DimTimePipeline {

  def populateIfEmpty(spark: SparkSession, outputNamespace: String): Unit = {
    val table = s"$outputNamespace.dim_time"
    val count = spark.sql(s"SELECT COUNT(*) FROM $table").collect()(0).getLong(0)
    if (count > 0L) {
      println(s"[DimTimePipeline] dim_time already has $count rows — skipping")
      return
    }

    val times = spark.sql("SELECT explode(sequence(0, 1439)) AS total_min")
      .withColumn("hour",   (col("total_min") / 60).cast(IntegerType))
      .withColumn("minute", (col("total_min") % 60).cast(IntegerType))
      .withColumn("time_key", col("hour") * 100 + col("minute"))
      .withColumn("time_label",
        concat(
          lpad(col("hour").cast("string"),   2, "0"),
          lit(":"),
          lpad(col("minute").cast("string"), 2, "0"),
          lit(" "),
          when(col("hour") < 12, lit("AM")).otherwise(lit("PM"))
        ))
      .withColumn("market_session",
        when(col("time_key").between(400,  929),  lit("PRE"))
        .when(col("time_key").between(930,  1559), lit("REGULAR"))
        .when(col("time_key").between(1600, 1959), lit("POST"))
        .otherwise(lit("CLOSED")))
      .withColumn("session_minute",
        when(col("time_key").between(930, 1559),
          col("total_min") - lit(9 * 60 + 30))
        .otherwise(lit(null).cast(IntegerType)))
      .withColumn("is_opening_hour", col("time_key").between(930,  1029))
      .withColumn("is_closing_hour", col("time_key").between(1500, 1559))
      .drop("total_min")

    times.writeTo(table).append()
    println(s"[DimTimePipeline] Populated dim_time (1440 rows)")
  }
}
