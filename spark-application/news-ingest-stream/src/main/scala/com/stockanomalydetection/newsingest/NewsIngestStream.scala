package com.stockanomalydetection.newsingest

import com.stockanomalydetection.newsingest.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.newsingest.pipeline.{BatchProgressListener, NewsPipeline}
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object NewsIngestStream {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession
      .builder()
      .appName("news-ingest-stream")
      .getOrCreate()

    spark.streams.addListener(BatchProgressListener)

    val config = AppConfig.fromEnv()
    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    logger.info(s"Starting news-ingest-stream: topic=${config.inputTopic} → ${config.outputTable}")

    val rawStream   = NewsPipeline.buildRawStream(spark, config)
    val transformed = NewsPipeline.transform(rawStream)
    val query       = NewsPipeline.write(transformed, config)

    query.awaitTermination()
  }
}
