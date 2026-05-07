package com.stockanomalydetection.newscleaner.config

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
      spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_catalog.normalized")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace gravitino_catalog.normalized: ${e.getMessage}", e)
    }

    try {
      spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_catalog.normalized.news_clean (
        article_id     STRING    NOT NULL  COMMENT 'MD5 of url — dedup key carried from bronze',
        dedup_hash     STRING    NOT NULL  COMMENT 'MD5 of title — headline-level dedup key',
        symbol         STRING              COMMENT 'Ticker symbol the article is about',
        source_name    STRING              COMMENT 'Normalized publisher name (trimmed, lower-cased)',
        title          STRING              COMMENT 'Article headline',
        description    STRING,
        url            STRING,
        category       STRING,
        published_at   TIMESTAMP           COMMENT 'Article publication timestamp (UTC)',
        fetched_at     TIMESTAMP,
        data_source    STRING    NOT NULL  COMMENT 'Always finnhub for this table',
        cleaned_at     TIMESTAMP NOT NULL  COMMENT 'Row cleaning timestamp (UTC)',
        published_date DATE                COMMENT 'Partition column derived from published_at'
      )
      USING iceberg
      PARTITIONED BY (published_date)
      TBLPROPERTIES (
        'write.distribution-mode'         = 'hash',
        'write.target-file-size-bytes'    = '134217728',
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create table gravitino_catalog.normalized.news_clean: ${e.getMessage}", e)
    }

    // Idempotent schema migration for tables created before the field-mapping fix.
    val table = "gravitino_catalog.normalized.news_clean"
    addColumnIfMissing(spark, table, "category", "STRING")
    dropColumnIfPresent(spark, table, "author")
    dropColumnIfPresent(spark, table, "content")
    dropColumnIfPresent(spark, table, "search_query")
  }

  private def addColumnIfMissing(spark: SparkSession, table: String, column: String, colType: String): Unit =
    try {
      spark.sql(s"ALTER TABLE $table ADD COLUMN $column $colType")
    } catch {
      case _: Exception => // column already exists — safe to ignore
    }

  private def dropColumnIfPresent(spark: SparkSession, table: String, column: String): Unit =
    try {
      spark.sql(s"ALTER TABLE $table DROP COLUMN $column")
    } catch {
      case _: Exception => // column already gone — safe to ignore
    }
}
