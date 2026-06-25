package com.stockanomalydetection.ruleenginecontext.config

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

  def ensureTableExists(spark: SparkSession): Unit = {
    try {
      spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_gold")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace gravitino_gold: ${e.getMessage}", e)
    }

    try {
      spark.sql("""
        CREATE TABLE IF NOT EXISTS gravitino_gold.rule_engine_context (
          symbol              STRING    NOT NULL  COMMENT 'Ticker symbol — Rule Engine cache key',
          as_of_date          DATE      NOT NULL  COMMENT 'Snapshot trading day (UTC calendar)',

          mean_return_20d     DOUBLE              COMMENT '20-day rolling mean of daily_return',
          std_return_20d      DOUBLE              COMMENT '20-day rolling std-dev of daily_return',
          mean_return_5d      DOUBLE              COMMENT '5-day rolling mean of daily_return',
          std_return_5d       DOUBLE              COMMENT '5-day rolling std-dev of daily_return',

          mean_volume_20d     DOUBLE              COMMENT '20-day rolling mean of volume',
          std_volume_20d      DOUBLE              COMMENT '20-day rolling std-dev of volume',
          mean_volume_5d      DOUBLE              COMMENT '5-day rolling mean of volume',

          bb_upper_20d        DOUBLE              COMMENT 'Bollinger upper (20d)',
          bb_lower_20d        DOUBLE              COMMENT 'Bollinger lower (20d)',
          bb_mid_20d          DOUBLE              COMMENT 'Bollinger mid (20d SMA)',
          atr_14              DOUBLE              COMMENT 'Average True Range (14)',
          rsi_14              DOUBLE              COMMENT 'RSI (14)',
          vwap_5d_avg         DOUBLE              COMMENT '5-day rolling mean of VWAP',

          updated_at          TIMESTAMP           COMMENT 'Row write timestamp (UTC)'
        )
        USING iceberg
        PARTITIONED BY (as_of_date)
        TBLPROPERTIES (
          'write.distribution-mode'          = 'hash',
          'write.target-file-size-bytes'     = '134217728',
          'write.format.default'             = 'parquet',
          'write.parquet.compression-codec'  = 'zstd',
          'write.metadata.compression-codec' = 'gzip'
        )
      """)
    } catch {
      case e: Exception =>
        throw new RuntimeException(
          s"Failed to create table gravitino_gold.rule_engine_context: ${e.getMessage}",
          e
        )
    }
  }
}
