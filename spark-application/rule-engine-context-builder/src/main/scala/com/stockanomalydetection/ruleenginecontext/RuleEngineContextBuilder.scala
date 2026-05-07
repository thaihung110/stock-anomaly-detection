package com.stockanomalydetection.ruleenginecontext

import com.stockanomalydetection.ruleenginecontext.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.ruleenginecontext.pipeline.RuleEngineContextPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object RuleEngineContextBuilder {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("rule-engine-context-builder")
      .getOrCreate()

    val config = AppConfig.fromEnv()

    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    logger.info(
      s"Starting rule-engine-context-builder: ${config.inputTable} → ${config.outputTable}"
    )

    RuleEngineContextPipeline.run(spark, config)

    logger.info("rule-engine-context-builder completed successfully")
    spark.stop()
  }
}
