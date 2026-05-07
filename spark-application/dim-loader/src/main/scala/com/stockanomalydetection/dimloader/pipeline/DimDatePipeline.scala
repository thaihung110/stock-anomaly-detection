package com.stockanomalydetection.dimloader.pipeline

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types.IntegerType

import java.sql.Date
import java.util.Calendar

object DimDatePipeline {

  def populateIfEmpty(spark: SparkSession, outputNamespace: String): Unit = {
    val table = s"$outputNamespace.dim_date"
    val count = spark.sql(s"SELECT COUNT(*) FROM $table").collect()(0).getLong(0)
    if (count > 0L) {
      println(s"[DimDatePipeline] dim_date already has $count rows — skipping")
      return
    }

    spark.udf.register("is_nyse_holiday", isNyseHoliday _)

    val dates = spark.sql("""
      SELECT explode(sequence(DATE '2000-01-01', DATE '2040-12-31', INTERVAL 1 DAY)) AS full_date
    """)
      .withColumn("date_key",
        date_format(col("full_date"), "yyyyMMdd").cast(IntegerType))
      // Spark dayofweek: 1=Sun…7=Sat → remap to 1=Mon…7=Sun
      .withColumn("day_of_week",
        ((dayofweek(col("full_date")) + 5) % 7 + 1).cast(IntegerType))
      .withColumn("day_name",      date_format(col("full_date"), "EEEE"))
      .withColumn("day_of_month",  dayofmonth(col("full_date")))
      .withColumn("day_of_year",   dayofyear(col("full_date")))
      .withColumn("week_of_year",  weekofyear(col("full_date")))
      .withColumn("month_number",  month(col("full_date")))
      .withColumn("month_name",    date_format(col("full_date"), "MMMM"))
      .withColumn("quarter",       quarter(col("full_date")))
      .withColumn("year",          year(col("full_date")))
      .withColumn("is_weekend",    col("day_of_week").isin(6, 7))
      .withColumn("is_us_market_holiday",
        callUDF("is_nyse_holiday", col("full_date")))
      .withColumn("is_trading_day",
        !col("is_weekend") && !col("is_us_market_holiday"))
      .withColumn("trading_day_number",
        when(col("is_trading_day"),
          count(when(col("is_trading_day"), lit(1)))
            .over(Window.partitionBy("year")
              .orderBy("full_date")
              .rowsBetween(Window.unboundedPreceding, 0))
        ).otherwise(lit(null).cast(IntegerType)))

    dates.writeTo(table).append()
    println(s"[DimDatePipeline] Populated dim_date (2000–2040, ~14976 rows)")
  }

  // NYSE holiday calculation for a given date.
  // Handles: New Year's, MLK Day, Presidents Day, Good Friday, Memorial Day,
  // Juneteenth (2022+), Independence Day, Labor Day, Thanksgiving, Christmas.
  private def isNyseHoliday(date: Date): Boolean = {
    if (date == null) return false
    val cal = Calendar.getInstance()
    cal.setTime(date)
    val y   = cal.get(Calendar.YEAR)
    val m   = cal.get(Calendar.MONTH) + 1 // 1-based
    val d   = cal.get(Calendar.DAY_OF_MONTH)
    val dow = cal.get(Calendar.DAY_OF_WEEK) // 1=Sun…7=Sat

    // New Year's Day (Jan 1, observed Mon if Sun, Fri if Sat)
    if (isObserved(y, 1, 1, m, d, dow)) return true

    // MLK Day: 3rd Monday in January
    if (m == 1 && dow == Calendar.MONDAY && nthWeekday(cal, 3)) return true

    // Presidents Day: 3rd Monday in February
    if (m == 2 && dow == Calendar.MONDAY && nthWeekday(cal, 3)) return true

    // Good Friday
    if (isGoodFriday(y, m, d)) return true

    // Memorial Day: last Monday in May
    if (m == 5 && dow == Calendar.MONDAY && isLastWeekdayOfMonth(cal)) return true

    // Juneteenth (Jun 19, from 2022; observed)
    if (y >= 2022 && isObserved(y, 6, 19, m, d, dow)) return true

    // Independence Day (Jul 4, observed)
    if (isObserved(y, 7, 4, m, d, dow)) return true

    // Labor Day: 1st Monday in September
    if (m == 9 && dow == Calendar.MONDAY && nthWeekday(cal, 1)) return true

    // Thanksgiving: 4th Thursday in November
    if (m == 11 && dow == Calendar.THURSDAY && nthWeekday(cal, 4)) return true

    // Christmas (Dec 25, observed)
    if (isObserved(y, 12, 25, m, d, dow)) return true

    false
  }

  // Returns true if (m, d, dow) matches the observed date of holiday on (hy, hm, hd).
  // Saturday → observed Friday; Sunday → observed Monday.
  private def isObserved(y: Int, hm: Int, hd: Int, m: Int, d: Int, dow: Int): Boolean = {
    val cal = Calendar.getInstance()
    cal.set(y, hm - 1, hd)
    val hdow = cal.get(Calendar.DAY_OF_WEEK)
    hdow match {
      case Calendar.SATURDAY => m == hm && d == hd - 1 // Friday before
      case Calendar.SUNDAY   =>
        // Monday after — may roll into next month
        val next = Calendar.getInstance()
        next.set(y, hm - 1, hd)
        next.add(Calendar.DAY_OF_MONTH, 1)
        m == next.get(Calendar.MONTH) + 1 && d == next.get(Calendar.DAY_OF_MONTH)
      case _                 => m == hm && d == hd
    }
  }

  // Returns true if the date is the Nth occurrence of its weekday in its month.
  private def nthWeekday(cal: Calendar, n: Int): Boolean = {
    val dom  = cal.get(Calendar.DAY_OF_MONTH)
    val week = (dom - 1) / 7 + 1
    week == n
  }

  // Returns true if this is the last occurrence of its weekday in its month.
  private def isLastWeekdayOfMonth(cal: Calendar): Boolean = {
    val copy = cal.clone().asInstanceOf[Calendar]
    copy.add(Calendar.DAY_OF_MONTH, 7)
    copy.get(Calendar.MONTH) != cal.get(Calendar.MONTH)
  }

  // Good Friday = Friday before Easter Sunday (Spencer/Knuth algorithm).
  private def isGoodFriday(y: Int, m: Int, d: Int): Boolean = {
    val (em, ed) = easter(y)
    val easterCal = Calendar.getInstance()
    easterCal.set(y, em - 1, ed)
    easterCal.add(Calendar.DAY_OF_MONTH, -2) // 2 days before Easter = Good Friday
    m == easterCal.get(Calendar.MONTH) + 1 && d == easterCal.get(Calendar.DAY_OF_MONTH)
  }

  // Returns (month, day) of Easter Sunday for year y using the Anonymous Gregorian algorithm.
  private def easter(y: Int): (Int, Int) = {
    val a = y % 19
    val b = y / 100
    val c = y % 100
    val d = b / 4
    val e = b % 4
    val f = (b + 8) / 25
    val g = (b - f + 1) / 3
    val h = (19 * a + b - d - g + 15) % 30
    val i = c / 4
    val k = c % 4
    val l = (32 + 2 * e + 2 * i - h - k) % 7
    val m = (a + 11 * h + 22 * l) / 451
    val month = (h + l - 7 * m + 114) / 31
    val day   = ((h + l - 7 * m + 114) % 31) + 1
    (month, day)
  }
}
