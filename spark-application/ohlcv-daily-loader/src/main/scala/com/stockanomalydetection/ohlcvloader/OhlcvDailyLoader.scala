package com.stockanomalydetection.ohlcvloader

import com.stockanomalydetection.ohlcvloader.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.ohlcvloader.pipeline.OhlcvPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object OhlcvDailyLoader {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("ohlcv-daily-loader")
      .getOrCreate()

    val config = AppConfig.fromEnv()

    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    val symbols    = config.loadSymbols()
    val watermarks = OhlcvPipeline.getWatermarks(spark, config.outputTable)

    logger.info(s"Starting OHLCV load: ${symbols.size} symbols, ${watermarks.size} with existing watermarks")

    OhlcvPipeline.run(
      spark          = spark,
      symbols        = symbols,
      watermarks     = watermarks,
      outputTable    = config.outputTable,
      fetchBatchSize = config.fetchBatchSize,
      backfillYears  = config.backfillYears
    )

    logger.info("ohlcv-daily-loader completed successfully")
    spark.stop()
  }
}
