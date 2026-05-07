package com.stockanomalydetection.newscleaner

import com.stockanomalydetection.newscleaner.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.newscleaner.pipeline.NewsCleanerPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object NewsCleanerMain {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("news-cleaner")
      .getOrCreate()

    val config = AppConfig.fromEnv()

    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    logger.info(s"Starting news-cleaner: ${config.inputTable} → ${config.outputTable}")

    val bronze  = NewsCleanerPipeline.readBronze(spark, config.inputTable)
    val cleaned = NewsCleanerPipeline.transform(bronze)
    NewsCleanerPipeline.writeToSilver(spark, cleaned, config.outputTable)

    logger.info("news-cleaner completed successfully")
    spark.stop()
  }
}
