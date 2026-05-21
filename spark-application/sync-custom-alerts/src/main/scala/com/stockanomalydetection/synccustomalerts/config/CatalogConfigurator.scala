package com.stockanomalydetection.synccustomalerts.config

import org.apache.spark.sql.SparkSession

object CatalogConfigurator {

  def configure(spark: SparkSession, cfg: AppConfig): Unit = {
    val icebergRestUri = cfg.gravitinoUri.stripSuffix("/") + "/iceberg/"
    val tokenUri =
      cfg.gravitinoOauthServerUri.stripSuffix("/") + "/" +
        cfg.gravitinoOauthTokenPath.stripPrefix("/")

    spark.conf.set("spark.sql.catalog.gravitino_catalog", "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.type", "rest")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.uri", icebergRestUri)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.warehouse", cfg.icebergWarehouse)

    spark.conf.set("spark.sql.catalog.gravitino_catalog.rest.auth.type", "oauth2")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.oauth2-server-uri", tokenUri)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.token-refresh-enabled", "true")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.credential", s"spark:${cfg.gravitinoOauthClientSecret}")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.scope", cfg.gravitinoOauthScope)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.token-exchange-enabled", "false")

    spark.conf.set("spark.sql.catalog.gravitino_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.s3.endpoint", cfg.minioEndpoint)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.s3.access-key-id", cfg.minioAccessKey)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.s3.secret-access-key", cfg.minioSecretKey)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.s3.path-style-access", "true")

    val hc = spark.sparkContext.hadoopConfiguration
    hc.set("fs.s3a.endpoint", cfg.minioEndpoint)
    hc.set("fs.s3a.access.key", cfg.minioAccessKey)
    hc.set("fs.s3a.secret.key", cfg.minioSecretKey)
  }

  def ensureTableExists(spark: SparkSession, cfg: AppConfig): Unit = {
    val parts     = cfg.factTable.split("\\.")
    val namespace = parts.dropRight(1).mkString(".")

    try {
      spark.sql(s"CREATE NAMESPACE IF NOT EXISTS $namespace")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace $namespace: ${e.getMessage}", e)
    }

    try {
      spark.sql(s"""
        CREATE TABLE IF NOT EXISTS ${cfg.factTable} (
          alert_id        STRING  NOT NULL  COMMENT 'UUID of the alert event',
          symbol          STRING  NOT NULL  COMMENT 'Ticker symbol',
          event_ts        STRING  NOT NULL  COMMENT 'ISO-8601 UTC timestamp when the alert fired',
          rule_name       STRING  NOT NULL  COMMENT 'Rule description (system rule name or custom field/op/threshold)',
          severity        STRING  NOT NULL  COMMENT 'Alert severity: INFO, MEDIUM, HIGH',
          triggered_value DOUBLE  NOT NULL  COMMENT 'Field value at trigger time',
          threshold       DOUBLE  NOT NULL  COMMENT 'Threshold that was crossed',
          alert_source    STRING  NOT NULL  COMMENT 'system (rule engine) or user_custom (sync job)',
          written_at      STRING  NOT NULL  COMMENT 'ISO-8601 UTC timestamp when the row was written'
        )
        USING iceberg
        TBLPROPERTIES (
          'write.format.default'            = 'parquet',
          'write.parquet.compression-codec' = 'zstd'
        )
      """)
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create table ${cfg.factTable}: ${e.getMessage}", e)
    }
  }
}
