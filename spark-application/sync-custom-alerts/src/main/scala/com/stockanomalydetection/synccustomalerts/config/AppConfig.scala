package com.stockanomalydetection.synccustomalerts.config

final case class AppConfig(
  // Gravitino / Iceberg / MinIO
  gravitinoUri:               String,
  minioEndpoint:              String,
  minioAccessKey:             String,
  minioSecretKey:             String,
  gravitinoOauthClientSecret: String,
  gravitinoOauthServerUri:    String,
  gravitinoOauthTokenPath:    String,
  gravitinoOauthScope:        String,
  // PostgreSQL JDBC
  jdbcUrl:                    String,
  pgUser:                     String,
  pgPassword:                 String,
  // Target Iceberg table and watermark config
  factTable:                  String,
  watermarkJobName:           String
)

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
    jdbcUrl                    = sys.env("JDBC_URL"),
    pgUser                     = sys.env("PG_USER"),
    pgPassword                 = sys.env("PG_PASSWORD"),
    factTable                  = sys.env.getOrElse("FACT_TABLE", "gravitino_gold.gold.fact_alert_history"),
    watermarkJobName           = sys.env.getOrElse("WATERMARK_JOB_NAME", "sync-custom-alerts")
  )
}
