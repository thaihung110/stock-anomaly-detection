package com.stockanomalydetection.tradesohlcv

import com.stockanomalydetection.tradesohlcv.config.{AppConfig, CatalogConfigurator}
import com.stockanomalydetection.tradesohlcv.pipeline.TradesOhlcvPipeline
import org.apache.spark.sql.SparkSession

object TradesOhlcvStream {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession
      .builder()
      .appName("trades-ohlcv-stream")
      .getOrCreate()

    spark.sparkContext.setLogLevel("WARN")

    val config = AppConfig.fromEnv()
    CatalogConfigurator.configure(spark)

    val rawStream = TradesOhlcvPipeline.buildRawStream(spark, config)
    val transformed = TradesOhlcvPipeline.transform(rawStream)
    val query = TradesOhlcvPipeline.write(transformed, config)

    query.awaitTermination()
  }
}
