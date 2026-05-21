package com.stockanomalydetection.dimloader.config

final case class AppConfig(
  gravitinoUri: String,
  icebergWarehouse: String,
  minioEndpoint: String,
  minioAccessKey: String,
  minioSecretKey: String,
  gravitinoOauthClientSecret: String,
  gravitinoOauthServerUri: String,
  gravitinoOauthTokenPath: String,
  gravitinoOauthScope: String,
  inputTable: String,
  outputNamespace: String
)

object AppConfig {
  def fromEnv(): AppConfig = AppConfig(
    gravitinoUri               = sys.env("GRAVITINO_URI"),
    icebergWarehouse           = sys.env.getOrElse("ICEBERG_WAREHOUSE", "gold"),
    minioEndpoint              = sys.env("MINIO_ENDPOINT"),
    minioAccessKey             = sys.env("MINIO_ACCESS_KEY"),
    minioSecretKey             = sys.env("MINIO_SECRET_KEY"),
    gravitinoOauthClientSecret = sys.env("GRAVITINO_OAUTH_CLIENT_SECRET"),
    gravitinoOauthServerUri    = sys.env.getOrElse("GRAVITINO_OAUTH_SERVER_URI", "http://openhouse-keycloak:8080"),
    gravitinoOauthTokenPath    = sys.env.getOrElse("GRAVITINO_OAUTH_TOKEN_PATH", "realms/master/protocol/openid-connect/token"),
    gravitinoOauthScope        = sys.env.getOrElse("GRAVITINO_OAUTH_SCOPE", "openid"),
    inputTable                 = sys.env.getOrElse("INPUT_TABLE", "gravitino_catalog.raw.raw_company_info"),
    outputNamespace            = sys.env.getOrElse("OUTPUT_NAMESPACE", "gravitino_catalog.gold")
  )
}
