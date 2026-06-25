package com.stockanomalydetection.ohlcvloader.config

import scala.io.Source

final case class AppConfig(
  gravitinoUri:               String,
  minioEndpoint:              String,
  minioAccessKey:             String,
  minioSecretKey:             String,
  gravitinoOauthClientSecret: String,
  gravitinoOauthServerUri:    String,
  gravitinoOauthTokenPath:    String,
  gravitinoOauthScope:        String,
  symbolsFile:                String,
  outputTable:                String,
  fetchBatchSize:             Int,
  backfillYears:              Int
) {
  def loadSymbols(): List[String] = {
    val src = Source.fromFile(symbolsFile)
    try
      src.getLines()
        .map(_.trim)
        .filter(l => l.nonEmpty && !l.startsWith("#"))
        .toList
    finally src.close()
  }
}

object AppConfig {
  def fromEnv(): AppConfig = AppConfig(
    gravitinoUri               = sys.env("GRAVITINO_URI"),
    minioEndpoint              = sys.env("MINIO_ENDPOINT"),
    minioAccessKey             = sys.env("MINIO_ACCESS_KEY"),
    minioSecretKey             = sys.env("MINIO_SECRET_KEY"),
    gravitinoOauthClientSecret = sys.env("GRAVITINO_OAUTH_CLIENT_SECRET"),
    gravitinoOauthServerUri    = sys.env.getOrElse("GRAVITINO_OAUTH_SERVER_URI", "http://openhouse-keycloak"),
    gravitinoOauthTokenPath    = sys.env.getOrElse("GRAVITINO_OAUTH_TOKEN_PATH", "realms/iceberg/protocol/openid-connect/token"),
    gravitinoOauthScope        = sys.env.getOrElse("GRAVITINO_OAUTH_SCOPE", "gravitino"),
    symbolsFile                = sys.env.getOrElse("SYMBOLS_FILE", "/opt/spark/conf/symbols.txt"),
    outputTable                = sys.env.getOrElse("OUTPUT_TABLE", "gravitino_bronze.raw.raw_ohlcv_daily"),
    fetchBatchSize             = sys.env.getOrElse("FETCH_BATCH_SIZE", "50").toInt,
    backfillYears              = sys.env.getOrElse("BACKFILL_YEARS", "20").toInt
  )
}
