package com.stockanomalydetection.newsingest.schema

import org.apache.spark.sql.types.{StringType, StructField, StructType}

object NewsSchema {
  // Matches the Kafka message contract published by finnhub-news-producer (schema.py → to_kafka_bytes).
  // NewsPipeline.transform renames these to the canonical bronze column names.
  val schema: StructType = StructType(
    Seq(
      StructField("article_id",      StringType, nullable = false),
      StructField("symbol",          StringType, nullable = false),
      StructField("headline",        StringType, nullable = false),  // → title
      StructField("summary",         StringType, nullable = true),   // → description
      StructField("url",             StringType, nullable = false),
      StructField("source",          StringType, nullable = true),   // → source_name
      StructField("category",        StringType, nullable = true),
      StructField("published_at_ms", StringType, nullable = false),  // epoch ms → published_at timestamp
      StructField("fetched_at_ms",   StringType, nullable = true)    // epoch ms → fetched_at timestamp
    )
  )
}
