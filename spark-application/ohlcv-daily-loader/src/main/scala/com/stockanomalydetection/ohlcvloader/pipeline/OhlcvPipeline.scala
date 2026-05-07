package com.stockanomalydetection.ohlcvloader.pipeline

import com.stockanomalydetection.ohlcvloader.schema.OhlcvRow
import java.time.LocalDate
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession
import scala.collection.mutable.ListBuffer

object OhlcvPipeline {
  private val logger = LogManager.getLogger(getClass)

  def getWatermarks(spark: SparkSession, table: String): Map[String, LocalDate] =
    try {
      spark.sql(s"SELECT symbol, MAX(trade_date) AS max_date FROM $table GROUP BY symbol")
        .collect()
        .map(r => r.getString(0) -> r.getDate(1).toLocalDate)
        .toMap
    } catch {
      case e: Exception =>
        logger.warn(s"Could not read watermarks from $table (first run?): ${e.getMessage}")
        Map.empty
    }

  def run(
    spark:         SparkSession,
    symbols:       List[String],
    watermarks:    Map[String, LocalDate],
    outputTable:   String,
    fetchBatchSize: Int,
    backfillYears: Int
  ): Unit = {
    val today  = LocalDate.now()
    val buffer = new ListBuffer[OhlcvRow]
    var totalFetched = 0
    var upToDate     = 0
    var noData       = 0

    symbols.foreach { sym =>
      val from = watermarks.get(sym) match {
        case Some(last) => last.plusDays(1)
        case None       => today.minusYears(backfillYears)
      }

      if (from.isAfter(today)) {
        logger.info(s"[$sym] Already up-to-date (${watermarks(sym)})")
        upToDate += 1
      } else {
        val rows = YahooFinanceClient.fetchOhlcv(sym, from, today)
        if (rows.nonEmpty) {
          logger.info(s"[$sym] Fetched ${rows.size} rows ($from → $today)")
          buffer ++= rows
          totalFetched += rows.size
        } else {
          logger.info(s"[$sym] No data returned ($from → $today)")
          noData += 1
        }
        // Flush every fetchBatchSize symbols to bound driver memory
        if (buffer.size >= fetchBatchSize * 300) {
          mergeRows(spark, buffer.toList, outputTable)
          buffer.clear()
        }
      }
    }

    if (buffer.nonEmpty) mergeRows(spark, buffer.toList, outputTable)

    logger.info(
      s"Run complete — fetched=$totalFetched rows, up_to_date=$upToDate symbols, no_data=$noData symbols"
    )
  }

  private def mergeRows(spark: SparkSession, rows: List[OhlcvRow], table: String): Unit = {
    import spark.implicits._
    rows.toDF().createOrReplaceTempView("incoming_batch")
    logger.info(s"Merging ${rows.size} rows into $table")
    spark.sql(s"""
      MERGE INTO $table AS t
      USING (SELECT * FROM incoming_batch) AS s
      ON t.symbol = s.symbol AND t.trade_date = s.trade_date
      WHEN MATCHED THEN UPDATE SET
        t.open         = s.open,
        t.high         = s.high,
        t.low          = s.low,
        t.close        = s.close,
        t.adj_close    = s.adj_close,
        t.volume       = s.volume,
        t.dividends    = s.dividends,
        t.stock_splits = s.stock_splits,
        t.source       = s.source,
        t.ingested_at  = s.ingested_at
      WHEN NOT MATCHED THEN INSERT *
    """)
  }
}