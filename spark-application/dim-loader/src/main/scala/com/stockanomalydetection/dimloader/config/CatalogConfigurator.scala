package com.stockanomalydetection.dimloader.config

import org.apache.spark.sql.SparkSession

object CatalogConfigurator {

  def configure(spark: SparkSession, cfg: AppConfig): Unit = {
    configureCatalog(spark, "gravitino_bronze", "bronze", cfg)
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

  def ensureTablesExist(spark: SparkSession): Unit = {
    spark.sql("CREATE NAMESPACE IF NOT EXISTS gravitino_gold.gold")

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_gold.gold.dim_symbol (
        symbol_key         INT       NOT NULL  COMMENT 'Surrogate key',
        symbol             STRING    NOT NULL  COMMENT 'Natural key — ticker symbol',
        company_name       STRING              COMMENT 'Full legal company name',
        exchange           STRING              COMMENT 'Listing exchange: NASDAQ, NYSE, etc.',
        sector             STRING              COMMENT 'GICS sector (e.g. Technology, Financials)',
        industry           STRING              COMMENT 'GICS industry (e.g. Semiconductors)',
        country            STRING              COMMENT 'Country of incorporation',
        currency           STRING              COMMENT 'Trading currency (USD, etc.)',
        market_cap         BIGINT              COMMENT 'Market capitalisation in USD',
        shares_outstanding BIGINT              COMMENT 'Total shares outstanding',
        beta               DOUBLE              COMMENT 'Beta relative to S&P 500',
        week_52_high       DOUBLE              COMMENT '52-week high price',
        week_52_low        DOUBLE              COMMENT '52-week low price',
        is_active          BOOLEAN   NOT NULL  COMMENT 'True for the current SCD2 record',
        effective_from     DATE      NOT NULL  COMMENT 'SCD2 validity start (inclusive)',
        effective_to       DATE                COMMENT 'SCD2 validity end (inclusive); NULL = current',
        source             STRING    NOT NULL  COMMENT 'Data source (finnhub)'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_gold.gold.dim_date (
        date_key             INT       NOT NULL  COMMENT 'Surrogate key in YYYYMMDD format',
        full_date            DATE      NOT NULL  COMMENT 'Calendar date',
        day_of_week          INT                 COMMENT '1=Monday … 7=Sunday',
        day_name             STRING              COMMENT 'Full day name (Monday, Tuesday, …)',
        day_of_month         INT                 COMMENT 'Day number within the month (1–31)',
        day_of_year          INT                 COMMENT 'Day number within the year (1–366)',
        week_of_year         INT                 COMMENT 'ISO week number (1–53)',
        month_number         INT                 COMMENT 'Month number (1–12)',
        month_name           STRING              COMMENT 'Full month name (January, …)',
        quarter              INT                 COMMENT 'Calendar quarter (1–4)',
        year                 INT                 COMMENT 'Calendar year',
        is_weekend           BOOLEAN             COMMENT 'True for Saturday and Sunday',
        is_us_market_holiday BOOLEAN             COMMENT 'True on NYSE-observed US holidays',
        is_trading_day       BOOLEAN             COMMENT 'True when NYSE is open (not weekend or holiday)',
        trading_day_number   INT                 COMMENT 'Sequential trading day within the year; NULL on non-trading days'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_gold.gold.dim_time (
        time_key       INT     NOT NULL  COMMENT 'Surrogate key in HHMM format (e.g. 0930)',
        hour           INT               COMMENT 'Hour component (0–23)',
        minute         INT               COMMENT 'Minute component (0–59)',
        time_label     STRING            COMMENT 'Human-readable label (e.g. 09:30 AM)',
        market_session STRING            COMMENT 'NYSE session: PRE, REGULAR, POST, or CLOSED',
        session_minute INT               COMMENT 'Minutes elapsed since 09:30 open; NULL outside REGULAR session',
        is_opening_hour BOOLEAN          COMMENT 'True for 09:30–10:29 ET',
        is_closing_hour BOOLEAN          COMMENT 'True for 15:00–15:59 ET'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_gold.gold.dim_anomaly_type (
        anomaly_type_key INT     NOT NULL  COMMENT 'Surrogate key',
        anomaly_type     STRING  NOT NULL  COMMENT 'Machine-readable type code (e.g. PRICE_SPIKE)',
        anomaly_category STRING            COMMENT 'Broad category: PRICE, VOLUME, VOLATILITY, MOMENTUM',
        description      STRING            COMMENT 'Human-readable description of the anomaly',
        typical_cause    STRING            COMMENT 'Common underlying causes',
        risk_level       STRING            COMMENT 'Indicative risk: LOW, MEDIUM, HIGH'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_gold.gold.dim_rule (
        rule_key            INT     NOT NULL  COMMENT 'Surrogate key',
        rule_code           STRING  NOT NULL  COMMENT 'Machine-readable rule code (e.g. PRICE_Z)',
        rule_name           STRING            COMMENT 'Human-readable rule name',
        formula_description STRING            COMMENT 'Plain-English formula description',
        threshold_default   DOUBLE            COMMENT 'Default trigger threshold used by the Rule Engine',
        severity_if_alone   STRING            COMMENT 'Severity when this rule fires in isolation'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)

    spark.sql("""
      CREATE TABLE IF NOT EXISTS gravitino_gold.gold.dim_news_category (
        category_key          INT     NOT NULL  COMMENT 'Surrogate key',
        category_code         STRING  NOT NULL  COMMENT 'LLM output label: NEWS_EXPLAINED, UNEXPLAINED, UNCERTAIN',
        category_name         STRING            COMMENT 'Human-readable category name',
        description           STRING            COMMENT 'What this category means in context of anomaly validation',
        typical_price_impact  STRING            COMMENT 'Indicative price impact: HIGH, MEDIUM, LOW, VARIES, N/A'
      )
      USING iceberg
      TBLPROPERTIES (
        'write.format.default'            = 'parquet',
        'write.parquet.compression-codec' = 'zstd'
      )
    """)
  }
}
