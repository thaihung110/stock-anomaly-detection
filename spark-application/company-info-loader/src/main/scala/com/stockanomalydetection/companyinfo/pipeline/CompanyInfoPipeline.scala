package com.stockanomalydetection.companyinfo.pipeline

import com.stockanomalydetection.companyinfo.schema.CompanyInfoRow
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.SparkSession

object CompanyInfoPipeline {
  private val logger = LogManager.getLogger(getClass)

  def run(
    spark:       SparkSession,
    symbols:     List[String],
    outputTable: String,
    apiKey:      String
  ): Unit = {
    var fetched = 0
    var skipped = 0

    val rows = symbols.flatMap { sym =>
      val result = FinnhubCompanyClient.fetchInfo(sym, apiKey)
      result match {
        case Some(_) =>
          logger.info(s"[$sym] Fetched company info from Finnhub")
          fetched += 1
        case None =>
          logger.warn(s"[$sym] No data returned from Finnhub, skipping")
          skipped += 1
      }
      result
    }

    logger.info(s"Fetch complete — fetched=$fetched, skipped=$skipped")

    if (rows.nonEmpty) mergeRows(spark, rows, outputTable)
    else logger.warn("No rows to merge — all symbols returned no data")
  }

  private def mergeRows(spark: SparkSession, rows: List[CompanyInfoRow], table: String): Unit = {
    import spark.implicits._
    rows.toDF().createOrReplaceTempView("incoming_company_info")
    logger.info(s"Merging ${rows.size} rows into $table")
    spark.sql(s"""
      MERGE INTO $table AS t
      USING (SELECT * FROM incoming_company_info) AS s
      ON t.symbol = s.symbol
      WHEN MATCHED THEN UPDATE SET
        t.short_name          = s.short_name,
        t.long_name           = s.long_name,
        t.exchange            = s.exchange,
        t.quote_type          = s.quote_type,
        t.sector              = s.sector,
        t.industry            = s.industry,
        t.country             = s.country,
        t.currency            = s.currency,
        t.website             = s.website,
        t.market_cap          = s.market_cap,
        t.beta                = s.beta,
        t.trailing_pe         = s.trailing_pe,
        t.forward_pe          = s.forward_pe,
        t.fifty_two_week_high = s.fifty_two_week_high,
        t.fifty_two_week_low  = s.fifty_two_week_low,
        t.fifty_day_average   = s.fifty_day_average,
        t.two_hundred_day_avg = s.two_hundred_day_avg,
        t.shares_outstanding  = s.shares_outstanding,
        t.dividend_yield      = s.dividend_yield,
        t.fetched_at          = s.fetched_at
      WHEN NOT MATCHED THEN INSERT *
    """)
    logger.info(s"Merge complete — ${rows.size} rows upserted into $table")
  }
}
