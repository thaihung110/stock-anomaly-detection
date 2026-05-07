package com.stockanomalydetection.factohlcv.config

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

  def ensureTableExists(spark: SparkSession): Unit = {
    try {
      spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_catalog.gold")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace gravitino_catalog.gold: ${e.getMessage}", e)
    }

    try {
      spark.sql("""
        CREATE TABLE IF NOT EXISTS gravitino_catalog.gold.fact_ohlcv_daily (
          symbol_key          INT       NOT NULL  COMMENT 'Surrogate key — FK to gold.dim_symbol',
          date_key            INT       NOT NULL  COMMENT 'Surrogate key in YYYYMMDD format — FK to gold.dim_date',

          open                DOUBLE    NOT NULL  COMMENT 'Opening price',
          high                DOUBLE    NOT NULL  COMMENT 'Daily high',
          low                 DOUBLE    NOT NULL  COMMENT 'Daily low',
          close               DOUBLE    NOT NULL  COMMENT 'Raw closing price',
          adj_close           DOUBLE    NOT NULL  COMMENT 'Split/dividend-adjusted close — canonical for return calculations',
          vwap                DOUBLE              COMMENT '(open+high+low+close)/4 — intraday VWAP proxy',

          volume              BIGINT    NOT NULL  COMMENT 'Share volume',
          dollar_volume       DOUBLE              COMMENT 'close × volume',

          daily_return        DOUBLE              COMMENT '(close - prev_close) / prev_close',
          log_return          DOUBLE              COMMENT 'ln(close / prev_close)',
          intraday_range_pct  DOUBLE              COMMENT '(high - low) / low',
          gap_pct             DOUBLE              COMMENT '(open - prev_close) / prev_close',

          mean_return_20d     DOUBLE              COMMENT '20-day rolling mean of daily_return',
          std_return_20d      DOUBLE              COMMENT '20-day rolling population std-dev of daily_return',
          mean_volume_20d     DOUBLE              COMMENT '20-day rolling mean of volume',
          std_volume_20d      DOUBLE              COMMENT '20-day rolling population std-dev of volume',
          price_zscore        DOUBLE              COMMENT 'daily_return z-score: daily_return / std_return_20d',
          volume_zscore       DOUBLE              COMMENT 'volume z-score: (volume - mean_volume_20d) / std_volume_20d',

          rsi_14              DOUBLE              COMMENT 'Relative Strength Index (14-period SMA-based)',
          macd_line           DOUBLE              COMMENT 'MACD line: EMA-12 minus EMA-26',
          macd_signal         DOUBLE              COMMENT 'MACD signal line: EMA-9 of macd_line',
          macd_histogram      DOUBLE              COMMENT 'MACD histogram: macd_line minus macd_signal',
          bb_upper            DOUBLE              COMMENT 'Bollinger Band upper: bb_mid + 2 × stddev(close, 20d)',
          bb_lower            DOUBLE              COMMENT 'Bollinger Band lower: bb_mid - 2 × stddev(close, 20d)',
          bb_mid              DOUBLE              COMMENT 'Bollinger Band middle: 20-day SMA of close',
          bb_position         DOUBLE              COMMENT '(close - bb_lower) / (bb_upper - bb_lower); 0=at lower, 1=at upper',
          atr_14              DOUBLE              COMMENT 'Average True Range (14-period SMA)',

          data_source         STRING              COMMENT 'Data source identifier (yfinance)',
          loaded_at           TIMESTAMP           COMMENT 'Row load timestamp (UTC)'
        )
        USING iceberg
        TBLPROPERTIES (
          'write.distribution-mode'         = 'hash',
          'write.target-file-size-bytes'    = '134217728',
          'write.format.default'            = 'parquet',
          'write.parquet.compression-codec' = 'zstd',
          'write.metadata.compression-codec'= 'gzip',
          'write.sort.order'                = 'symbol_key ASC NULLS LAST, date_key ASC NULLS LAST'
        )
      """)
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create table gravitino_catalog.gold.fact_ohlcv_daily: ${e.getMessage}", e)
    }
  }
}
