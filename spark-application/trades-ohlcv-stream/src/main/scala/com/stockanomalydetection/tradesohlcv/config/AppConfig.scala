package com.stockanomalydetection.tradesohlcv.config

final case class AppConfig(
    kafkaBootstrapServers: String,
    checkpointLocation: String,
    outputTable: String,
    inputTopic: String,
    triggerInterval: String
)

object AppConfig {
  def fromEnv(): AppConfig =
    AppConfig(
      kafkaBootstrapServers = sys.env("KAFKA_BOOTSTRAP_SERVERS"),
      checkpointLocation = sys.env("CHECKPOINT_LOCATION"),
      outputTable = "gravitino_catalog.silver.ohlcv_1min",
      inputTopic = "raw.stock.trades",
      triggerInterval = "60 seconds"
    )
}
