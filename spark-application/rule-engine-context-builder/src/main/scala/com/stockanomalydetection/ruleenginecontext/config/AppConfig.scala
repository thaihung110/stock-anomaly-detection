package com.stockanomalydetection.ruleenginecontext.config

/** Optional `asOfDateKey` (YYYYMMDD) for backfill; when None, pipeline uses UTC yesterday. */
final case class AppConfig(
  gravitinoUri:                String,
  icebergWarehouse:            String,
  minioEndpoint:               String,
  minioAccessKey:              String,
  minioSecretKey:              String,
  gravitinoOauthClientSecret:  String,
  gravitinoOauthServerUri:     String,
  gravitinoOauthTokenPath:     String,
  gravitinoOauthScope:         String,
  inputTable:                  String,
  dimSymbolTable:              String,
  outputTable:                 String,
  asOfDateKey:                 Option[Int]
)

object AppConfig {
  def fromEnv(): AppConfig = AppConfig(
    gravitinoUri               = sys.env("GRAVITINO_URI"),
    icebergWarehouse           = sys.env("ICEBERG_WAREHOUSE"),
    minioEndpoint              = sys.env("MINIO_ENDPOINT"),
    minioAccessKey             = sys.env("MINIO_ACCESS_KEY"),
    minioSecretKey             = sys.env("MINIO_SECRET_KEY"),
    gravitinoOauthClientSecret = sys.env("GRAVITINO_OAUTH_CLIENT_SECRET"),
    gravitinoOauthServerUri    = sys.env.getOrElse("GRAVITINO_OAUTH_SERVER_URI", "http://openhouse-keycloak"),
    gravitinoOauthTokenPath    = sys.env.getOrElse("GRAVITINO_OAUTH_TOKEN_PATH", "realms/iceberg/protocol/openid-connect/token"),
    gravitinoOauthScope        = sys.env.getOrElse("GRAVITINO_OAUTH_SCOPE", "gravitino"),
    inputTable                 = sys.env.getOrElse("INPUT_TABLE", "gravitino_catalog.gold.fact_ohlcv_daily"),
    dimSymbolTable             = sys.env.getOrElse("DIM_SYMBOL_TABLE", "gravitino_catalog.gold.dim_symbol"),
    outputTable                = sys.env.getOrElse("OUTPUT_TABLE", "gravitino_catalog.gold.rule_engine_context"),
    asOfDateKey                = sys.env.get("AS_OF_DATE_KEY").flatMap(s => scala.util.Try(s.toInt).toOption)
  )
}
