package com.stockanomalydetection.ohlcvcleaner

import com.stockanomalydetection.ohlcvcleaner.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.ohlcvcleaner.pipeline.OhlcvCleanerPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object OhlcvDailyCleaner {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("ohlcv-daily-cleaner")
      .getOrCreate()

    val config = AppConfig.fromEnv()

    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    logger.info(s"Starting ohlcv-daily-cleaner: ${config.inputTable} → ${config.outputTable}")

    val bronze  = OhlcvCleanerPipeline.readBronze(spark, config.inputTable)
    val cleaned = OhlcvCleanerPipeline.transform(bronze)
    OhlcvCleanerPipeline.mergeInto(spark, cleaned, config.outputTable)

    logger.info("ohlcv-daily-cleaner completed successfully")
    spark.stop()
  }
}
