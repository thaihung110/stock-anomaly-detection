package com.stockanomalydetection.newsingest.config

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
    spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_bronze.raw")

    // New deployments get the correct schema from the start.
    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_bronze.raw.raw_news_articles (
        article_id     STRING    NOT NULL COMMENT 'MD5 of url — dedup key',
        symbol         STRING,
        source_name    STRING,
        title          STRING,
        description    STRING,
        url            STRING,
        category       STRING,
        published_at   TIMESTAMP,
        fetched_at     TIMESTAMP,
        published_date DATE      COMMENT 'Partition column derived from published_at'
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

    // Idempotent schema migration for tables created before the field-mapping fix.
    // Each statement is wrapped so a re-run on an already-migrated table is a no-op.
    val table = "gravitino_bronze.raw.raw_news_articles"
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
