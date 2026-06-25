package com.stockanomalydetection.ohlcvcleaner.config

import org.apache.spark.sql.SparkSession

object CatalogConfigurator {

  def configure(spark: SparkSession, cfg: AppConfig): Unit = {
    configureCatalog(spark, "gravitino_bronze", "bronze", cfg)
    configureCatalog(spark, "gravitino_silver", "silver", cfg)

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
      spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_silver.normalized")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace gravitino_silver.normalized: ${e.getMessage}", e)
    }

    try {
      spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_silver.normalized.ohlcv_daily (
        symbol        STRING    NOT NULL  COMMENT 'Ticker symbol',
        trade_date    DATE      NOT NULL  COMMENT 'Trading day',
        open          DOUBLE    NOT NULL  COMMENT 'Opening price',
        high          DOUBLE    NOT NULL  COMMENT 'Daily high',
        low           DOUBLE    NOT NULL  COMMENT 'Daily low',
        close         DOUBLE    NOT NULL  COMMENT 'Raw closing price',
        adj_close     DOUBLE    NOT NULL  COMMENT 'Split/dividend-adjusted close — canonical for return calculations',
        volume        BIGINT    NOT NULL  COMMENT 'Share volume',
        dividends     DOUBLE    NOT NULL  COMMENT 'Dividend amount on this date, 0.0 otherwise',
        stock_splits  DOUBLE    NOT NULL  COMMENT 'Split ratio on this date, 0.0 otherwise',
        vwap_estimate DOUBLE              COMMENT '(open+high+low+close)/4 — intraday VWAP proxy',
        data_source   STRING    NOT NULL  COMMENT 'Always yfinance for this table',
        is_complete   BOOLEAN   NOT NULL  COMMENT 'True when all OHLCV fields are present and non-zero',
        cleaned_at    TIMESTAMP NOT NULL  COMMENT 'Row cleaning timestamp (UTC)'
      )
      USING iceberg
      PARTITIONED BY (months(trade_date))
      TBLPROPERTIES (
        'write.distribution-mode'         = 'hash',
        'write.target-file-size-bytes'    = '134217728',
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create table gravitino_silver.normalized.ohlcv_daily: ${e.getMessage}", e)
    }
  }
}
