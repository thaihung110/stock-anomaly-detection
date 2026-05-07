package com.stockanomalydetection.dimloader.pipeline

import org.apache.spark.sql.SparkSession

object DimStaticPipeline {

  def seedIfEmpty(spark: SparkSession, outputNamespace: String): Unit = {
    seedAnomalyType(spark, outputNamespace)
    seedRule(spark, outputNamespace)
    seedNewsCategory(spark, outputNamespace)
  }

  private def isEmpty(spark: SparkSession, table: String): Boolean =
    spark.sql(s"SELECT COUNT(*) FROM $table").collect()(0).getLong(0) == 0L

  private def seedAnomalyType(spark: SparkSession, ns: String): Unit = {
    val table = s"$ns.dim_anomaly_type"
    if (!isEmpty(spark, table)) return

    spark.sql(s"""
      INSERT INTO $table VALUES
        (1, 'PRICE_SPIKE',        'PRICE',      'Sudden upward price movement beyond normal range',          'Earnings surprise, M&A news, short squeeze',         'HIGH'),
        (2, 'PRICE_DROP',         'PRICE',      'Sudden downward price movement beyond normal range',        'Earnings miss, negative news, broad sell-off',       'HIGH'),
        (3, 'VOLUME_SURGE',       'VOLUME',     'Trading volume far exceeds historical average',             'Institutional accumulation, news catalyst, rumour',  'MEDIUM'),
        (4, 'BOLLINGER_BREAKOUT', 'VOLATILITY', 'Price breaks outside Bollinger Band boundaries',            'Momentum continuation or mean-reversion setup',      'MEDIUM'),
        (5, 'RSI_EXTREME',        'MOMENTUM',   'RSI reaches overbought or oversold territory',              'Trend exhaustion, potential reversal signal',         'MEDIUM'),
        (6, 'INTRADAY_RANGE',     'VOLATILITY', 'High-low spread exceeds 5% of open price within one day',  'Earnings, macro events, low-liquidity conditions',   'LOW')
    """)
    println(s"[DimStaticPipeline] Seeded dim_anomaly_type (6 rows)")
  }

  private def seedRule(spark: SparkSession, ns: String): Unit = {
    val table = s"$ns.dim_rule"
    if (!isEmpty(spark, table)) return

    spark.sql(s"""
      INSERT INTO $table VALUES
        (1, 'PRICE_Z',      'Price Z-Score',        'z = daily_return / std_return_20d; triggers when |z| > threshold',       3.0,  'MEDIUM'),
        (2, 'VOLUME_Z',     'Volume Z-Score',       'z = (volume - mean_volume_20d) / std_volume_20d; triggers when z > thr', 3.0,  'MEDIUM'),
        (3, 'VOLUME_RATIO', 'Volume Ratio 20d',     'volume / mean_volume_20d; triggers when ratio > threshold',               3.5,  'MEDIUM'),
        (4, 'BOLLINGER',    'Bollinger Breakout',   'bb_pos = (close - bb_lower) / (bb_upper - bb_lower); outside [0,1]',     1.0,  'MEDIUM'),
        (5, 'RSI',          'RSI Extreme',          'RSI-14; triggers when RSI > 80 (overbought) or < 20 (oversold)',          80.0, 'LOW'),
        (6, 'INTRADAY',     'Intraday Range Pct',   '(high - low) / low * 100; triggers when range pct > threshold',          5.0,  'LOW')
    """)
    println(s"[DimStaticPipeline] Seeded dim_rule (6 rows)")
  }

  private def seedNewsCategory(spark: SparkSession, ns: String): Unit = {
    val table = s"$ns.dim_news_category"
    if (!isEmpty(spark, table)) return

    spark.sql(s"""
      INSERT INTO $table VALUES
        (1, 'NEWS_EXPLAINED', 'News Explained', 'Anomaly is fully explained by recent news coverage; alert suppressed', 'HIGH'),
        (2, 'UNEXPLAINED',    'Unexplained',    'No news catalyst found; anomaly forwarded to alert channel',           'VARIES'),
        (3, 'UNCERTAIN',      'Uncertain',      'LLM could not determine news relevance with confidence',               'N/A')
    """)
    println(s"[DimStaticPipeline] Seeded dim_news_category (3 rows)")
  }
}
