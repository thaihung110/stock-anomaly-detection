package com.stockanomalydetection.newsingest.config

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
      outputTable = "gravitino_catalog.bronze.raw_news_articles",
      inputTopic = "raw.stock.news",
      triggerInterval = "30 seconds"
    )
}
