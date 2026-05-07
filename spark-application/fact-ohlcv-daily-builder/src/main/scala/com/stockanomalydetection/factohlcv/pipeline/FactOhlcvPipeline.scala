package com.stockanomalydetection.factohlcv.pipeline

import com.stockanomalydetection.factohlcv.config.AppConfig
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types.DoubleType
import org.apache.spark.sql.{DataFrame, SparkSession}

object FactOhlcvPipeline {
  private val logger = LogManager.getLogger(getClass)

  // ── MACD UDF ─────────────────────────────────────────────────────────────────
  // Computes EMA-12, EMA-26, MACD line, signal-9, and histogram for an ordered
  // array of closing prices.  Returns parallel arrays: (macd_line, signal, histogram).
  // Called once per symbol group via collect_list → explode-back pattern.

  private def emaArray(prices: Array[Double], period: Int): Array[Double] = {
    val k      = 2.0 / (period + 1)
    val result = new Array[Double](prices.length)
    var ema    = prices(0)
    result(0)  = ema
    for (i <- 1 until prices.length) {
      ema       = prices(i) * k + ema * (1.0 - k)
      result(i) = ema
    }
    result
  }

  // UDF: given ordered close prices, return parallel arrays for macd_line / macd_signal / macd_histogram.
  // The function returns a struct with three double arrays; each is later zipped back onto rows.
  private val macdUdf = udf { (closes: Seq[Double]) =>
    if (closes == null || closes.length < 26) {
      val nulls = closes.map(_ => null.asInstanceOf[Double])
      (nulls, nulls, nulls)
    } else {
      val arr     = closes.toArray
      val ema12   = emaArray(arr, 12)
      val ema26   = emaArray(arr, 26)
      val macdLine = ema12.zip(ema26).map { case (a, b) => a - b }
      val signal  = emaArray(macdLine, 9)
      val histo   = macdLine.zip(signal).map { case (m, s) => m - s }
      (macdLine.toSeq, signal.toSeq, histo.toSeq)
    }
  }

  // ── Stage 1: Read inputs ──────────────────────────────────────────────────────

  def readSilver(spark: SparkSession, inputTable: String): DataFrame = {
    logger.info(s"Reading silver table: $inputTable")
    spark.table(inputTable)
  }

  def readDimSymbol(spark: SparkSession, dimSymbolTable: String): DataFrame = {
    logger.info(s"Reading dim_symbol (active records only): $dimSymbolTable")
    spark.table(dimSymbolTable)
      .filter(col("is_active") === true)
      .select(col("symbol"), col("symbol_key"))
  }

  def readDimDate(spark: SparkSession, dimDateTable: String): DataFrame = {
    logger.info(s"Reading dim_date: $dimDateTable")
    spark.table(dimDateTable)
      .select(col("full_date"), col("date_key"))
  }

  // ── Stage 2: Join dimensions ──────────────────────────────────────────────────

  def joinDims(
    silver:    DataFrame,
    dimSymbol: DataFrame,
    dimDate:   DataFrame
  ): DataFrame = {
    logger.info("Joining silver → dim_symbol and dim_date")
    silver
      .join(broadcast(dimSymbol), Seq("symbol"), "inner")
      .join(broadcast(dimDate), silver("trade_date") === dimDate("full_date"), "inner")
      .drop(dimDate("full_date"))
  }

  // ── Stage 3: Window computations ─────────────────────────────────────────────

  def computeIndicators(df: DataFrame): DataFrame = {
    logger.info("Computing rolling stats and technical indicators")

    // Base windows ordered by date_key (INT YYYYMMDD — exact sort order, no Date parsing)
    val w    = Window.partitionBy("symbol_key").orderBy("date_key")
    val w14  = w.rowsBetween(-13, 0)   // 14-row SMA window
    val w20  = w.rowsBetween(-19, 0)   // 20-row rolling window

    // Previous close — used for return calculations
    val prevClose = lag(col("adj_close"), 1).over(w)

    // ── Returns ──────────────────────────────────────────────────────────────
    val withReturns = df
      .withColumn("_prev_close",       prevClose)
      .withColumn("daily_return",
        when(col("_prev_close").isNotNull && col("_prev_close") =!= 0.0,
          (col("adj_close") - col("_prev_close")) / col("_prev_close")
        ).otherwise(lit(null).cast(DoubleType)))
      .withColumn("log_return",
        when(col("_prev_close").isNotNull && col("_prev_close") > 0.0 && col("adj_close") > 0.0,
          log(col("adj_close") / col("_prev_close"))
        ).otherwise(lit(null).cast(DoubleType)))
      .withColumn("gap_pct",
        when(col("_prev_close").isNotNull && col("_prev_close") =!= 0.0,
          (col("open") - col("_prev_close")) / col("_prev_close")
        ).otherwise(lit(null).cast(DoubleType)))
      .withColumn("intraday_range_pct",
        when(col("low") =!= 0.0,
          (col("high") - col("low")) / col("low")
        ).otherwise(lit(null).cast(DoubleType)))
      .withColumn("dollar_volume",     col("adj_close") * col("volume").cast(DoubleType))

    // ── Rolling stats ─────────────────────────────────────────────────────────
    val withRolling = withReturns
      .withColumn("mean_return_20d", avg(col("daily_return")).over(w20))
      .withColumn("std_return_20d",  stddev_pop(col("daily_return")).over(w20))
      .withColumn("mean_volume_20d", avg(col("volume").cast(DoubleType)).over(w20))
      .withColumn("std_volume_20d",  stddev_pop(col("volume").cast(DoubleType)).over(w20))

    // ── Z-scores ──────────────────────────────────────────────────────────────
    val withZscores = withRolling
      .withColumn("price_zscore",
        when(col("std_return_20d").isNotNull && col("std_return_20d") =!= 0.0,
          col("daily_return") / col("std_return_20d")
        ).otherwise(lit(null).cast(DoubleType)))
      .withColumn("volume_zscore",
        when(col("std_volume_20d").isNotNull && col("std_volume_20d") =!= 0.0,
          (col("volume").cast(DoubleType) - col("mean_volume_20d")) / col("std_volume_20d")
        ).otherwise(lit(null).cast(DoubleType)))

    // ── Bollinger Bands (20d, 2σ) ─────────────────────────────────────────────
    val withBB = withZscores
      .withColumn("bb_mid",   avg(col("adj_close")).over(w20))
      .withColumn("_bb_std",  stddev_pop(col("adj_close")).over(w20))
      .withColumn("bb_upper", col("bb_mid") + lit(2.0) * col("_bb_std"))
      .withColumn("bb_lower", col("bb_mid") - lit(2.0) * col("_bb_std"))
      .withColumn("bb_position",
        when(
          col("bb_upper").isNotNull && col("bb_lower").isNotNull &&
          (col("bb_upper") - col("bb_lower")) =!= 0.0,
          (col("adj_close") - col("bb_lower")) / (col("bb_upper") - col("bb_lower"))
        ).otherwise(lit(null).cast(DoubleType)))

    // ── RSI-14 (SMA-based) ────────────────────────────────────────────────────
    // Wilder's original uses a custom EMA; SMA-based is a close approximation
    // at daily granularity and is consistent with rule_engine_context builder.
    val withRsi = withBB
      .withColumn("_avg_gain_14",
        avg(when(col("daily_return") > 0.0, col("daily_return")).otherwise(0.0)).over(w14))
      .withColumn("_avg_loss_14",
        avg(when(col("daily_return") < 0.0, -col("daily_return")).otherwise(0.0)).over(w14))
      .withColumn("rsi_14",
        when(col("_avg_loss_14").isNotNull && col("_avg_loss_14") =!= 0.0,
          lit(100.0) - (lit(100.0) / (lit(1.0) + col("_avg_gain_14") / col("_avg_loss_14")))
        ).when(col("_avg_loss_14") === 0.0, lit(100.0))
        .otherwise(lit(null).cast(DoubleType)))

    // ── ATR-14 (14-period SMA of True Range) ──────────────────────────────────
    val withAtr = withRsi
      .withColumn("_true_range",
        when(col("_prev_close").isNotNull,
          greatest(
            col("high") - col("low"),
            abs(col("high") - col("_prev_close")),
            abs(col("low")  - col("_prev_close"))
          )
        ).otherwise(col("high") - col("low")))
      .withColumn("atr_14", avg(col("_true_range")).over(w14))

    // ── MACD (EMA-12 / EMA-26 / Signal-9) ────────────────────────────────────
    // collect_list preserves insertion order when combined with the window ordering.
    // We collect ordered closes per symbol, compute MACD arrays via UDF, then
    // zip back onto rows using a row-number index within each partition.
    val rn = Window.partitionBy("symbol_key").orderBy("date_key")

    val withRowNum = withAtr
      .withColumn("_row_num", row_number().over(rn))

    // Collect ordered close prices and compute MACD arrays per symbol
    val macdArrays = withRowNum
      .groupBy("symbol_key")
      .agg(
        sort_array(
          collect_list(struct(col("_row_num"), col("adj_close")))
        ).alias("_ordered_closes_struct")
      )
      .withColumn("_close_arr",
        transform(col("_ordered_closes_struct"), x => x.getField("adj_close")))
      .withColumn("_macd_result", macdUdf(col("_close_arr")))
      .withColumn("_macd_line_arr",  col("_macd_result._1"))
      .withColumn("_macd_signal_arr", col("_macd_result._2"))
      .withColumn("_macd_histo_arr",  col("_macd_result._3"))
      // Explode arrays with index to rejoin on (symbol_key, _row_num)
      .withColumn("_idx_macd",
        posexplode(col("_macd_line_arr")))
      .select(
        col("symbol_key"),
        (col("_idx_macd.pos") + lit(1)).alias("_row_num"),  // posexplode is 0-based
        col("_idx_macd.col").alias("macd_line"),
        element_at(col("_macd_signal_arr"), col("_idx_macd.pos") + lit(1)).alias("macd_signal"),
        element_at(col("_macd_histo_arr"),  col("_idx_macd.pos") + lit(1)).alias("macd_histogram")
      )

    val withMacd = withRowNum
      .join(macdArrays, Seq("symbol_key", "_row_num"), "left")

    // ── Final projection ──────────────────────────────────────────────────────
    withMacd
      .withColumn("data_source", lit("yfinance"))
      .withColumn("loaded_at",   current_timestamp())
      .withColumnRenamed("vwap_estimate", "vwap")
      .select(
        col("symbol_key"),
        col("date_key"),
        col("open"),
        col("high"),
        col("low"),
        col("close"),
        col("adj_close"),
        col("vwap"),
        col("volume"),
        col("dollar_volume"),
        col("daily_return"),
        col("log_return"),
        col("intraday_range_pct"),
        col("gap_pct"),
        col("mean_return_20d"),
        col("std_return_20d"),
        col("mean_volume_20d"),
        col("std_volume_20d"),
        col("price_zscore"),
        col("volume_zscore"),
        col("rsi_14"),
        col("macd_line"),
        col("macd_signal"),
        col("macd_histogram"),
        col("bb_upper"),
        col("bb_lower"),
        col("bb_mid"),
        col("bb_position"),
        col("atr_14"),
        col("data_source"),
        col("loaded_at")
      )
  }

  // ── Stage 4: MERGE INTO fact_ohlcv_daily ─────────────────────────────────────

  def mergeInto(spark: SparkSession, df: DataFrame, outputTable: String): Unit = {
    val cached   = df.cache()
    val rowCount = cached.count()
    cached.createOrReplaceTempView("incoming_fact_ohlcv")
    logger.info(s"Merging $rowCount rows into $outputTable")

    try {
      spark.sql(s"""
        MERGE INTO $outputTable AS t
        USING (SELECT * FROM incoming_fact_ohlcv) AS s
        ON t.symbol_key = s.symbol_key AND t.date_key = s.date_key
        WHEN MATCHED THEN UPDATE SET
          t.open               = s.open,
          t.high               = s.high,
          t.low                = s.low,
          t.close              = s.close,
          t.adj_close          = s.adj_close,
          t.vwap               = s.vwap,
          t.volume             = s.volume,
          t.dollar_volume      = s.dollar_volume,
          t.daily_return       = s.daily_return,
          t.log_return         = s.log_return,
          t.intraday_range_pct = s.intraday_range_pct,
          t.gap_pct            = s.gap_pct,
          t.mean_return_20d    = s.mean_return_20d,
          t.std_return_20d     = s.std_return_20d,
          t.mean_volume_20d    = s.mean_volume_20d,
          t.std_volume_20d     = s.std_volume_20d,
          t.price_zscore       = s.price_zscore,
          t.volume_zscore      = s.volume_zscore,
          t.rsi_14             = s.rsi_14,
          t.macd_line          = s.macd_line,
          t.macd_signal        = s.macd_signal,
          t.macd_histogram     = s.macd_histogram,
          t.bb_upper           = s.bb_upper,
          t.bb_lower           = s.bb_lower,
          t.bb_mid             = s.bb_mid,
          t.bb_position        = s.bb_position,
          t.atr_14             = s.atr_14,
          t.data_source        = s.data_source,
          t.loaded_at          = s.loaded_at
        WHEN NOT MATCHED THEN INSERT *
      """)
    } catch {
      case e: Exception =>
        cached.unpersist()
        throw new RuntimeException(s"MERGE INTO $outputTable failed: ${e.getMessage}", e)
    }

    cached.unpersist()
    logger.info(s"Merge complete — $rowCount rows upserted into $outputTable")
  }

  // ── Entry point ───────────────────────────────────────────────────────────────

  def run(spark: SparkSession, cfg: AppConfig): Unit = {
    val silver    = readSilver(spark, cfg.inputTable)
    val dimSymbol = readDimSymbol(spark, cfg.dimSymbolTable)
    val dimDate   = readDimDate(spark, cfg.dimDateTable)

    val joined    = joinDims(silver, dimSymbol, dimDate)
    val enriched  = computeIndicators(joined)

    mergeInto(spark, enriched, cfg.outputTable)
  }
}
