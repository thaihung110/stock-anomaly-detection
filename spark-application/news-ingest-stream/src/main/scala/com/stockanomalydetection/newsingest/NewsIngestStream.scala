package com.stockanomalydetection.newsingest

import com.stockanomalydetection.newsingest.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.newsingest.pipeline.NewsPipeline
import org.apache.spark.sql.SparkSession

object NewsIngestStream {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession
      .builder()
      .appName("news-ingest-stream")
      .getOrCreate()

    spark.sparkContext.setLogLevel("WARN")

    val config = AppConfig.fromEnv()
    CatalogConfigurator.configure(spark)

    val rawStream = NewsPipeline.buildRawStream(spark, config)
    val transformed = NewsPipeline.transform(rawStream)
    val query = NewsPipeline.write(transformed, config)

    query.awaitTermination()
  }
}
