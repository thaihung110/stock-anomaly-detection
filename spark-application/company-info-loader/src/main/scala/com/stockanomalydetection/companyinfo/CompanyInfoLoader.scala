package com.stockanomalydetection.companyinfo

import com.stockanomalydetection.companyinfo.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.companyinfo.pipeline.CompanyInfoPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object CompanyInfoLoader {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("company-info-loader")
      .getOrCreate()

    val config = AppConfig.fromEnv()

    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    val symbols = config.loadSymbols()
    logger.info(s"Starting company info load for ${symbols.size} symbols → ${config.outputTable}")

    CompanyInfoPipeline.run(
      spark       = spark,
      symbols     = symbols,
      outputTable = config.outputTable,
      apiKey      = config.finnhubApiKey
    )

    logger.info("company-info-loader completed successfully")
    spark.stop()
  }
}
