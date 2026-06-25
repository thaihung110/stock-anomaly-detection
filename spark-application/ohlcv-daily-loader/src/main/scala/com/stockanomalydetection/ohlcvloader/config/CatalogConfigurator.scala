package com.stockanomalydetection.ohlcvloader.config

import org.apache.spark.sql.SparkSession

object CatalogConfigurator {

  def configure(spark: SparkSession, cfg: AppConfig): Unit = {
    configureCatalog(spark, "gravitino_bronze", "bronze", cfg)

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
      spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_bronze.raw")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace gravitino_bronze.raw: ${e.getMessage}", e)
    }

    try {
      spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_bronze.raw.raw_ohlcv_daily (
        symbol       STRING    NOT NULL COMMENT 'Ticker symbol',
        trade_date   DATE      NOT NULL COMMENT 'Trading day',
        open         DOUBLE    COMMENT 'Opening price',
        high         DOUBLE    COMMENT 'Daily high',
        low          DOUBLE    COMMENT 'Daily low',
        close        DOUBLE    COMMENT 'Closing price',
        adj_close    DOUBLE    COMMENT 'Split/dividend-adjusted close',
        volume       BIGINT    COMMENT 'Share volume',
        dividends    DOUBLE    COMMENT 'Dividend amount on this date, 0 otherwise',
        stock_splits DOUBLE    COMMENT 'Split ratio on this date (e.g. 4.0 for 4:1), 0 otherwise',
        source       STRING    NOT NULL COMMENT 'Data source identifier',
        ingested_at  TIMESTAMP NOT NULL COMMENT 'Row insertion timestamp'
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
        throw new RuntimeException(s"Failed to create table gravitino_bronze.raw.raw_ohlcv_daily: ${e.getMessage}", e)
    }
  }
}
