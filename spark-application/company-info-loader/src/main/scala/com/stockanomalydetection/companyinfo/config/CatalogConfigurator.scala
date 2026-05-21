package com.stockanomalydetection.companyinfo.config

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
      spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_catalog.raw")
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create namespace gravitino_catalog.raw: ${e.getMessage}", e)
    }

    // Unpartitioned — 50 rows is a tiny table; partitioning would add unnecessary overhead.
    // Source: Finnhub /stock/profile2 + /stock/metric?metric=all
    // Fields marked NULL are not available on Finnhub free tier.
    try {
      spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_catalog.raw.raw_company_info (
        symbol              STRING    NOT NULL COMMENT 'Ticker symbol',
        short_name          STRING    COMMENT 'Company name — Finnhub profile2.name',
        long_name           STRING    COMMENT 'Same as short_name (Finnhub has one name field)',
        exchange            STRING    COMMENT 'Exchange name — Finnhub profile2.exchange',
        quote_type          STRING    COMMENT 'Asset type — NULL (not in Finnhub free tier)',
        sector              STRING    COMMENT 'Sector — NULL (Finnhub has industry only via finnhubIndustry)',
        industry            STRING    COMMENT 'Industry — Finnhub profile2.finnhubIndustry',
        country             STRING    COMMENT 'Country of incorporation — Finnhub profile2.country',
        currency            STRING    COMMENT 'Trading currency — Finnhub profile2.currency',
        website             STRING    COMMENT 'Corporate website — Finnhub profile2.weburl',
        market_cap          BIGINT    COMMENT 'Market cap in USD — Finnhub profile2.marketCapitalization * 1e6',
        beta                DOUBLE    COMMENT 'Beta — Finnhub metric.beta',
        trailing_pe         DOUBLE    COMMENT 'Trailing P/E (TTM) — Finnhub metric.peBasicExclExtraTTM',
        forward_pe          DOUBLE    COMMENT 'Forward P/E — NULL (not in Finnhub free tier)',
        fifty_two_week_high DOUBLE    COMMENT '52-week high — Finnhub metric.52WeekHigh',
        fifty_two_week_low  DOUBLE    COMMENT '52-week low — Finnhub metric.52WeekLow',
        fifty_day_average   DOUBLE    COMMENT '50-day SMA — Finnhub metric.50DayMA',
        two_hundred_day_avg DOUBLE    COMMENT '200-day SMA — Finnhub metric.200DayMA',
        shares_outstanding  BIGINT    COMMENT 'Shares outstanding in units — Finnhub profile2.shareOutstanding * 1e6',
        dividend_yield      DOUBLE    COMMENT 'Indicated annual dividend yield — Finnhub metric.dividendYieldIndicatedAnnual',
        fetched_at          TIMESTAMP NOT NULL COMMENT 'Row fetch timestamp (UTC)'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)
    } catch {
      case e: Exception =>
        throw new RuntimeException(s"Failed to create table gravitino_catalog.bronze.raw_company_info: ${e.getMessage}", e)
    }
  }
}
