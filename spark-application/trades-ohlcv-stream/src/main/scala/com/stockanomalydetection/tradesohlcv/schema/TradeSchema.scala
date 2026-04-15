package com.stockanomalydetection.tradesohlcv.schema

import org.apache.spark.sql.types.{ArrayType, DoubleType, LongType, StringType, StructField, StructType}

object TradeSchema {
  val schema: StructType = StructType(
    Seq(
      StructField("symbol", StringType, nullable = false),
      StructField("price", DoubleType, nullable = false),
      StructField("volume", LongType, nullable = false),
      StructField("timestamp_ms", LongType, nullable = false),
      StructField("conditions", ArrayType(StringType), nullable = true)
    )
  )
}
