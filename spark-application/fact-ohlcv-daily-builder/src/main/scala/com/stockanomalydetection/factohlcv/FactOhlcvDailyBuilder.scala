package com.stockanomalydetection.factohlcv

import com.stockanomalydetection.factohlcv.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.factohlcv.pipeline.FactOhlcvPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object FactOhlcvDailyBuilder {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("fact-ohlcv-daily-builder")
      .getOrCreate()

    val config = AppConfig.fromEnv()

    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    logger.info(
      s"Starting fact-ohlcv-daily-builder: ${config.inputTable} → ${config.outputTable}"
    )

    FactOhlcvPipeline.run(spark, config)

    logger.info("fact-ohlcv-daily-builder completed successfully")
    spark.stop()
  }
}
