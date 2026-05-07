package com.stockanomalydetection.tradesohlcv.config

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
    spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_catalog.normalized")

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_catalog.normalized.ohlcv_1min (
        bar_ts      TIMESTAMP NOT NULL COMMENT '1-minute window start (UTC)',
        symbol      STRING    NOT NULL,
        open        DOUBLE,
        high        DOUBLE,
        low         DOUBLE,
        close       DOUBLE,
        volume      BIGINT,
        trade_count INT,
        vwap        DOUBLE,
        bar_date    DATE      NOT NULL COMMENT 'UTC date of bar_ts — partition column'
      )
      USING iceberg
      PARTITIONED BY (bar_date, symbol)
      TBLPROPERTIES (
        'write.distribution-mode'         = 'hash',
        'write.target-file-size-bytes'    = '134217728',
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)
  }
}
