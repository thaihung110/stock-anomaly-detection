package com.stockanomalydetection.synccustomalerts.config

import org.apache.spark.sql.SparkSession

object CatalogConfigurator {

  def configure(spark: SparkSession, cfg: AppConfig): Unit = {
    configureCatalog(spark, "gravitino_gold", "gold", cfg)

    val hc = spark.sparkContext.hadoopConfiguration
    hc.set("fs.s3a.endpoint", cfg.minioEndpoint)
    hc.set("fs.s3a.access.key", cfg.minioAccessKey)
    hc.set("fs.s3a.secret.key", cfg.minioSecretKey)
  }

  private def configureCatalog(spark: SparkSession, name: String, warehouse: String, cfg: AppConfig): Unit = {
    val icebergRestUri = cfg.gravitinoUri.stripSuffix("/") + "/iceberg/"
    val tokenUri =
      cfg.gravitinoOauthServerUri.stripSuffix("/") + "/" +
        cfg.gravitinoOauthTokenPath.stripPrefix("/")

    spark.conf.set(s"spark.sql.catalog.$name", "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set(s"spark.sql.catalog.$name.type", "rest")
    spark.conf.set(s"spark.sql.catalog.$name.uri", icebergRestUri)
    spark.conf.set(s"spark.sql.catalog.$name.warehouse", warehouse)

    spark.conf.set(s"spark.sql.catalog.$name.rest.auth.type", "oauth2")
    spark.conf.set(s"spark.sql.catalog.$name.oauth2-server-uri", tokenUri)
    spark.conf.set(s"spark.sql.catalog.$name.token-refresh-enabled", "true")
    spark.conf.set(s"spark.sql.catalog.$name.credential", s"spark:${cfg.gravitinoOauthClientSecret}")
    spark.conf.set(s"spark.sql.catalog.$name.scope", cfg.gravitinoOauthScope)
    spark.conf.set(s"spark.sql.catalog.$name.token-exchange-enabled", "false")

    spark.conf.set(s"spark.sql.catalog.$name.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    spark.conf.set(s"spark.sql.catalog.$name.s3.endpoint", cfg.minioEndpoint)
    spark.conf.set(s"spark.sql.catalog.$name.s3.access-key-id", cfg.minioAccessKey)
    spark.conf.set(s"spark.sql.catalog.$name.s3.secret-access-key", cfg.minioSecretKey)
    spark.conf.set(s"spark.sql.catalog.$name.s3.path-style-access", "true")
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
          written_at      STRING  NOT NULL  COMMENT 'ISO-8601 UTC timestamp when the row was written',
          user_id         STRING            COMMENT 'Owner of the custom alert (NULL for system alerts)'
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
