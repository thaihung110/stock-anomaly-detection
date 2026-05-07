package com.stockanomalydetection.dimloader

import com.stockanomalydetection.dimloader.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.dimloader.pipeline.{
  DimDatePipeline, DimStaticPipeline, DimSymbolPipeline, DimTimePipeline
}
import org.apache.spark.sql.SparkSession

object DimLoaderApp {

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("dim-loader")
      .getOrCreate()

    val cfg = AppConfig.fromEnv()
    CatalogConfigurator.configure(spark, cfg)
    CatalogConfigurator.ensureTablesExist(spark)

    // Static lookup dims: idempotent, skipped if already seeded
    DimStaticPipeline.seedIfEmpty(spark, cfg.outputNamespace)

    // Time and date dims: pre-generated, idempotent
    DimTimePipeline.populateIfEmpty(spark, cfg.outputNamespace)
    DimDatePipeline.populateIfEmpty(spark, cfg.outputNamespace)

    // Symbol dim: SCD2 weekly refresh from bronze
    DimSymbolPipeline.run(
      spark,
      cfg.inputTable,
      s"${cfg.outputNamespace}.dim_symbol"
    )

    spark.stop()
  }
}
