package com.stockanomalydetection.synccustomalerts

import com.stockanomalydetection.synccustomalerts.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.synccustomalerts.pipeline.SyncCustomAlertsPipeline
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object SyncCustomAlerts {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("sync-custom-alerts")
      .getOrCreate()

    val cfg = AppConfig.fromEnv()
    CatalogConfigurator.configure(spark, cfg)
    CatalogConfigurator.ensureTableExists(spark, cfg)

    logger.info(s"Starting sync-custom-alerts: user_alert_events → ${cfg.factTable}")
    SyncCustomAlertsPipeline.run(spark, cfg)
    logger.info("sync-custom-alerts completed successfully")
    spark.stop()
  }
}
