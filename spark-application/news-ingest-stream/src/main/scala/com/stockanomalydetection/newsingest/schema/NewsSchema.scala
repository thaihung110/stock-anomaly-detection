package com.stockanomalydetection.newsingest.schema

import org.apache.spark.sql.types.{StringType, StructField, StructType}

object NewsSchema {
  val schema: StructType = StructType(
    Seq(
      StructField("symbol", StringType, nullable = true),
      StructField("source_name", StringType, nullable = true),
      StructField("author", StringType, nullable = true),
      StructField("title", StringType, nullable = false),
      StructField("description", StringType, nullable = true),
      StructField("url", StringType, nullable = false),
      StructField("published_at", StringType, nullable = false),
      StructField("content", StringType, nullable = true),
      StructField("search_query", StringType, nullable = true),
      StructField("fetched_at", StringType, nullable = true)
    )
  )
}
