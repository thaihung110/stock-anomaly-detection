package com.stockanomalydetection.tradesohlcv.config

final case class AppConfig(
  gravitinoUri:               String,
  icebergWarehouse:           String,
  minioEndpoint:              String,
  minioAccessKey:             String,
  minioSecretKey:             String,
  gravitinoOauthClientSecret: String,
  gravitinoOauthServerUri:    String,
  gravitinoOauthTokenPath:    String,
  gravitinoOauthScope:        String,
  kafkaBootstrapServers:      String,
  checkpointLocation:         String,
  outputTable:                String,
  inputTopic:                 String,
  triggerInterval:            String,
  maxOffsetsPerTrigger:       String,
  writeDistributionMode:      String,
  targetFileSizeBytes:        String,
  fanoutEnabled:              String
)

object AppConfig {
  def fromEnv(): AppConfig =
    AppConfig(
      gravitinoUri               = sys.env("GRAVITINO_URI"),
      icebergWarehouse           = sys.env("ICEBERG_WAREHOUSE"),
      minioEndpoint              = sys.env("MINIO_ENDPOINT"),
      minioAccessKey             = sys.env("MINIO_ACCESS_KEY"),
      minioSecretKey             = sys.env("MINIO_SECRET_KEY"),
      gravitinoOauthClientSecret = sys.env("GRAVITINO_OAUTH_CLIENT_SECRET"),
      gravitinoOauthServerUri    = sys.env.getOrElse("GRAVITINO_OAUTH_SERVER_URI", "http://openhouse-keycloak"),
      gravitinoOauthTokenPath    = sys.env.getOrElse("GRAVITINO_OAUTH_TOKEN_PATH", "realms/iceberg/protocol/openid-connect/token"),
      gravitinoOauthScope        = sys.env.getOrElse("GRAVITINO_OAUTH_SCOPE", "gravitino"),
      kafkaBootstrapServers      = sys.env("KAFKA_BOOTSTRAP_SERVERS"),
      checkpointLocation         = sys.env("CHECKPOINT_LOCATION"),
      outputTable                = sys.env.getOrElse("OUTPUT_TABLE", "gravitino_catalog.normalized.ohlcv_1min"),
      inputTopic                 = sys.env.getOrElse("INPUT_TOPIC", "raw.stock.trades"),
      triggerInterval            = sys.env.getOrElse("TRIGGER_INTERVAL", "60 seconds"),
      maxOffsetsPerTrigger       = sys.env.getOrElse("KAFKA_MAX_OFFSETS_PER_TRIGGER", "20000"),
      writeDistributionMode      = sys.env.getOrElse("ICEBERG_WRITE_DISTRIBUTION_MODE", "hash"),
      targetFileSizeBytes        = sys.env.getOrElse("ICEBERG_TARGET_FILE_SIZE_BYTES", "134217728"),
      fanoutEnabled              = sys.env.getOrElse("ICEBERG_FANOUT_ENABLED", "false")
    )
}
