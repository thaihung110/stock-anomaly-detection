package com.stockanomalydetection.tradesohlcv

import com.stockanomalydetection.tradesohlcv.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.tradesohlcv.pipeline.{BatchProgressListener, TradesOhlcvPipeline}
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object TradesOhlcvStream {
  private val logger = LogManager.getLogger(getClass)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession
      .builder()
      .appName("trades-ohlcv-stream")
      .getOrCreate()

    spark.streams.addListener(BatchProgressListener)

    val config = AppConfig.fromEnv()
    CatalogConfigurator.configure(spark, config)
    CatalogConfigurator.ensureTableExists(spark)

    logger.info(s"Starting trades-ohlcv-stream: topic=${config.inputTopic} → ${config.outputTable}")

    val rawStream   = TradesOhlcvPipeline.buildRawStream(spark, config)
    val transformed = TradesOhlcvPipeline.transform(rawStream)
    val query       = TradesOhlcvPipeline.write(transformed, config)

    query.awaitTermination()
  }
}
