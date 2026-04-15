CREATE TABLE dim_symbol (
    symbol_key          INTEGER       NOT NULL PRIMARY KEY,   -- Surrogate key
    symbol              VARCHAR(20)   NOT NULL,               -- Natural key (ticker)
    company_name        VARCHAR(200),
    exchange            VARCHAR(20),                          -- "NASDAQ", "NYSE"
    sector              VARCHAR(100),                         -- "Technology", "Financials"
    industry            VARCHAR(200),                         -- "Semiconductors", "Banks"
    country             VARCHAR(50),
    currency            VARCHAR(5),
    market_cap          BIGINT,
    shares_outstanding  BIGINT,
    beta                DOUBLE PRECISION,
    week_52_high        DOUBLE PRECISION,
    week_52_low         DOUBLE PRECISION,
    -- SCD Type 2 tracking
    is_active           BOOLEAN       DEFAULT TRUE,
    effective_from      DATE          NOT NULL,
    effective_to        DATE,                                 -- NULL = current record
    source              VARCHAR(20)   DEFAULT 'yfinance'
);

-- dim_date: Date Dimension (Pre-populated calendar)
CREATE TABLE dim_date (
    date_key            INTEGER       NOT NULL PRIMARY KEY,   -- YYYYMMDD (e.g. 20260325)
    full_date           DATE          NOT NULL,
    day_of_week         INTEGER,                              -- 1=Monday to 7=Sunday
    day_name            VARCHAR(10),
    day_of_month        INTEGER,
    day_of_year         INTEGER,
    week_of_year        INTEGER,
    month_number        INTEGER,
    month_name          VARCHAR(10),
    quarter             INTEGER,                              -- 1 to 4
    year                INTEGER,
    is_weekend          BOOLEAN,
    is_us_market_holiday BOOLEAN,
    is_trading_day      BOOLEAN,                              -- TRUE = NYSE open
    trading_day_number  INTEGER                               -- sequential trading day in year
);

-- dim_time: Time Dimension (Intraday slot granularity)
CREATE TABLE dim_time (
    time_key            INTEGER       NOT NULL PRIMARY KEY,   -- HHMM (e.g. 0930)
    hour                INTEGER,
    minute              INTEGER,
    time_label          VARCHAR(8),                           -- "09:30 AM"
    market_session      VARCHAR(20),                          -- "PRE", "REGULAR", "POST", "CLOSED"
    session_minute      INTEGER,                              -- Minutes from session open (0=09:30)
    is_opening_hour     BOOLEAN,                              -- 09:30-10:30
    is_closing_hour     BOOLEAN                               -- 15:00-16:00
);

-- dim_anomaly_type: Anomaly Type Taxonomy
CREATE TABLE dim_anomaly_type (
    anomaly_type_key    INTEGER       NOT NULL PRIMARY KEY,
    anomaly_type        VARCHAR(30)   NOT NULL,               -- "PRICE_SPIKE", "PRICE_DROP", etc.
    anomaly_category    VARCHAR(20),                          -- "PRICE", "VOLUME", "VOLATILITY", "MOMENTUM"
    description         TEXT,
    typical_cause       TEXT,
    risk_level          VARCHAR(10)                           -- "LOW", "MEDIUM", "HIGH"
);

-- dim_rule: Rule Engine Rule Definitions
CREATE TABLE dim_rule (
    rule_key            INTEGER       NOT NULL PRIMARY KEY,
    rule_code           VARCHAR(30)   NOT NULL,               -- "PRICE_Z", "VOLUME_Z", etc.
    rule_name           VARCHAR(100),
    formula_description TEXT,                                 -- Human-readable formula
    threshold_default   DOUBLE PRECISION,
    severity_if_alone   VARCHAR(10)                           -- Severity when triggered alone
);

-- dim_news_category: News Category Taxonomy
CREATE TABLE dim_news_category (
    category_key        INTEGER       NOT NULL PRIMARY KEY,
    category_code       VARCHAR(30)   NOT NULL,               -- "earnings", "m_and_a", "macro", etc.
    category_name       VARCHAR(100),
    description         TEXT,
    typical_price_impact VARCHAR(20)                          -- "HIGH", "MEDIUM", "LOW", "VARIES", "N/A"
);

-- FACT TABLES

-- fact_ohlcv_daily: Central Daily OHLCV Fact Table
-- Grain: 1 row = 1 symbol x 1 trading day
CREATE TABLE fact_ohlcv_daily (
    -- Surrogate Keys (FK to dimensions)
    symbol_key          INTEGER       NOT NULL,               -- FK -> dim_symbol
    date_key            INTEGER       NOT NULL,               -- FK -> dim_date

    -- Measures - Price
    open                DOUBLE PRECISION NOT NULL,
    high                DOUBLE PRECISION NOT NULL,
    low                 DOUBLE PRECISION NOT NULL,
    close               DOUBLE PRECISION NOT NULL,
    adj_close           DOUBLE PRECISION,
    vwap                DOUBLE PRECISION,

    -- Measures - Volume
    volume              BIGINT        NOT NULL,
    dollar_volume       DOUBLE PRECISION,                     -- close x volume

    -- Measures - Derived Returns
    daily_return        DOUBLE PRECISION,                     -- (close - prev_close) / prev_close
    log_return          DOUBLE PRECISION,                     -- ln(close / prev_close)
    intraday_range_pct  DOUBLE PRECISION,                     -- (high - low) / low
    gap_pct             DOUBLE PRECISION,                     -- (open - prev_close) / prev_close

    -- Measures - Rolling Statistics (pre-computed by Spark)
    mean_return_20d     DOUBLE PRECISION,
    std_return_20d      DOUBLE PRECISION,
    mean_volume_20d     DOUBLE PRECISION,
    std_volume_20d      DOUBLE PRECISION,
    price_zscore        DOUBLE PRECISION,                     -- daily_return z-score
    volume_zscore       DOUBLE PRECISION,                     -- volume z-score

    -- Measures - Technical Indicators
    rsi_14              DOUBLE PRECISION,
    macd_line           DOUBLE PRECISION,
    macd_signal         DOUBLE PRECISION,
    macd_histogram      DOUBLE PRECISION,
    bb_upper            DOUBLE PRECISION,
    bb_lower            DOUBLE PRECISION,
    bb_mid              DOUBLE PRECISION,
    bb_position         DOUBLE PRECISION,                     -- (close-bb_lower)/(bb_upper-bb_lower)
    atr_14              DOUBLE PRECISION,

    -- Metadata
    data_source         VARCHAR(20),
    loaded_at           TIMESTAMP,

    PRIMARY KEY (symbol_key, date_key),
    FOREIGN KEY (symbol_key) REFERENCES dim_symbol(symbol_key),
    FOREIGN KEY (date_key)   REFERENCES dim_date(date_key)
);

-- fact_anomaly_daily: Anomaly Events Fact Table
-- Grain: 1 row = 1 anomaly event (1 symbol x 1 detection timestamp)
CREATE TABLE fact_anomaly_daily (
    -- Surrogate Keys
    anomaly_id          BIGINT        NOT NULL PRIMARY KEY,   -- Surrogate key (auto-generated)
    symbol_key          INTEGER       NOT NULL,               -- FK -> dim_symbol
    date_key            INTEGER       NOT NULL,               -- FK -> dim_date
    time_key            INTEGER       NOT NULL,               -- FK -> dim_time
    anomaly_type_key    INTEGER       NOT NULL,               -- FK -> dim_anomaly_type
    news_category_key   INTEGER,                              -- FK -> dim_news_category (filled by LLM)

    -- Detection Context
    detected_at         TIMESTAMP     NOT NULL,
    granularity         VARCHAR(5)    DEFAULT '1d',           -- "1m", "1d"
    severity            VARCHAR(10),                          -- "LOW", "MEDIUM", "HIGH"
    triggered_rules     TEXT,                                 -- Comma-separated rule_code list

    -- Measures - Raw Metrics at Detection Time
    price               DOUBLE PRECISION,
    daily_return        DOUBLE PRECISION,
    price_zscore        DOUBLE PRECISION,
    volume              BIGINT,
    volume_zscore       DOUBLE PRECISION,
    volume_ratio_20d    DOUBLE PRECISION,
    intraday_range_pct  DOUBLE PRECISION,
    vwap_deviation_pct  DOUBLE PRECISION,
    rsi_14              DOUBLE PRECISION,
    bb_position         DOUBLE PRECISION,

    -- Layer 1 (LLM) Output
    llm_judgement       VARCHAR(30),                          -- "NEWS_EXPLAINED", "DATA_ERROR", "UNEXPLAINED"
    llm_explanation     TEXT,
    news_articles_found INTEGER,
    llm_processed_at    TIMESTAMP,

    -- Portfolio Flag
    portfolio_flag      BOOLEAN       DEFAULT FALSE,          -- TRUE if symbol in user watchlist

    -- Alert Delivery
    alert_sent          BOOLEAN       DEFAULT FALSE,
    alert_sent_at       TIMESTAMP,

    FOREIGN KEY (symbol_key)        REFERENCES dim_symbol(symbol_key),
    FOREIGN KEY (date_key)          REFERENCES dim_date(date_key),
    FOREIGN KEY (time_key)          REFERENCES dim_time(time_key),
    FOREIGN KEY (anomaly_type_key)  REFERENCES dim_anomaly_type(anomaly_type_key),
    FOREIGN KEY (news_category_key) REFERENCES dim_news_category(category_key)
);

-- fact_alert_history: Alert Delivery Fact Table
-- Grain: 1 row = 1 Telegram alert delivery event
CREATE TABLE fact_alert_history (
    -- Surrogate Keys
    alert_id            BIGINT        NOT NULL PRIMARY KEY,   -- Surrogate key (auto-generated)
    anomaly_id          BIGINT        NOT NULL,               -- FK -> fact_anomaly_daily
    symbol_key          INTEGER       NOT NULL,               -- FK -> dim_symbol (denormalized)
    date_key            INTEGER       NOT NULL,               -- FK -> dim_date
    time_key            INTEGER       NOT NULL,               -- FK -> dim_time

    -- Measures
    alerted_at          TIMESTAMP     NOT NULL,
    delivery_channel    VARCHAR(20)   DEFAULT 'telegram',
    delivery_status     VARCHAR(20),                          -- "DELIVERED", "FAILED", "PENDING"
    llm_judgement       VARCHAR(30),                          -- denormalized for quick filter
    severity            VARCHAR(10),

    -- Telegram specific
    telegram_msg_id     BIGINT,
    telegram_chat_id    BIGINT,

    -- Acknowledge (future feature)
    acknowledged_at     TIMESTAMP,
    acknowledged_by     VARCHAR(100),

    FOREIGN KEY (anomaly_id)  REFERENCES fact_anomaly_daily(anomaly_id),
    FOREIGN KEY (symbol_key)  REFERENCES dim_symbol(symbol_key),
    FOREIGN KEY (date_key)    REFERENCES dim_date(date_key),
    FOREIGN KEY (time_key)    REFERENCES dim_time(time_key)
);

-- bridge_anomaly_rule: Bridge table to connect fact_anomaly_daily to dim_rule (Many-to-Many)
CREATE TABLE bridge_anomaly_rule (
    anomaly_id          BIGINT        NOT NULL,
    rule_key            INTEGER       NOT NULL,
    
    PRIMARY KEY (anomaly_id, rule_key),
    FOREIGN KEY (anomaly_id) REFERENCES fact_anomaly_daily(anomaly_id),
    FOREIGN KEY (rule_key)   REFERENCES dim_rule(rule_key)
);

-- OPERATIONAL / CONTEXT TABLE

-- rule_engine_context: Rolling Stats for Rule Engine (operational, not analytical)
-- Grain: 1 row = 1 symbol x 1 trading day
CREATE TABLE rule_engine_context (
    symbol              VARCHAR(20)   NOT NULL,
    as_of_date          DATE          NOT NULL,

    -- Rolling return stats (20-day)
    mean_return_20d     DOUBLE PRECISION,
    std_return_20d      DOUBLE PRECISION,
    mean_return_5d      DOUBLE PRECISION,
    std_return_5d       DOUBLE PRECISION,

    -- Rolling volume stats
    mean_volume_20d     DOUBLE PRECISION,
    std_volume_20d      DOUBLE PRECISION,
    mean_volume_5d      DOUBLE PRECISION,

    -- Technical indicator baselines
    bb_upper_20d        DOUBLE PRECISION,
    bb_lower_20d        DOUBLE PRECISION,
    bb_mid_20d          DOUBLE PRECISION,
    atr_14              DOUBLE PRECISION,
    rsi_14              DOUBLE PRECISION,
    vwap_5d_avg         DOUBLE PRECISION,

    updated_at          TIMESTAMP,
    PRIMARY KEY (symbol, as_of_date)
);

