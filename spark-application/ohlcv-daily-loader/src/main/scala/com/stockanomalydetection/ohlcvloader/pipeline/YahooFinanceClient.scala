package com.stockanomalydetection.ohlcvloader.pipeline

import com.stockanomalydetection.ohlcvloader.schema.OhlcvRow
import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.sql.{Date, Timestamp}
import java.time.{Duration, Instant, LocalDate, ZoneOffset}
import org.apache.logging.log4j.LogManager
import org.json4s._
import org.json4s.jackson.JsonMethods._

object YahooFinanceClient {
  private val logger = LogManager.getLogger(getClass)

  private val http = HttpClient.newBuilder()
    .connectTimeout(Duration.ofSeconds(15))
    .build()

  private val BASE_URL   = "https://query1.finance.yahoo.com/v8/finance/chart"
  private val MAX_RETRY  = 3
  private val SOURCE     = "yfinance"

  implicit private val formats: Formats = DefaultFormats

  def fetchOhlcv(symbol: String, fromDate: LocalDate, toDate: LocalDate): List[OhlcvRow] = {
    val period1 = fromDate.atStartOfDay(ZoneOffset.UTC).toEpochSecond
    val period2 = toDate.plusDays(1).atStartOfDay(ZoneOffset.UTC).toEpochSecond
    val url = s"$BASE_URL/$symbol?period1=$period1&period2=$period2&interval=1d&includeAdjustedClose=true"
    doFetch(symbol, url, MAX_RETRY)
  }

  private def doFetch(symbol: String, url: String, retries: Int): List[OhlcvRow] =
    try {
      val req = HttpRequest.newBuilder(URI.create(url))
        .header("User-Agent", "Mozilla/5.0")
        .header("Accept", "application/json")
        .GET()
        .build()
      val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
      resp.statusCode() match {
        case 200 => parseBody(symbol, resp.body())
        case 404 =>
          logger.warn(s"[$symbol] Not found on Yahoo Finance, skipping")
          List.empty
        case 429 if retries > 0 =>
          logger.warn(s"[$symbol] Rate-limited, waiting 5s ($retries retries left)")
          Thread.sleep(5000)
          doFetch(symbol, url, retries - 1)
        case code if retries > 0 =>
          logger.warn(s"[$symbol] HTTP $code, retrying ($retries left)")
          Thread.sleep(2000)
          doFetch(symbol, url, retries - 1)
        case code =>
          logger.error(s"[$symbol] HTTP $code after all retries")
          List.empty
      }
    } catch {
      case e: Exception if retries > 0 =>
        logger.warn(s"[$symbol] ${e.getMessage}, retrying ($retries left)")
        Thread.sleep(2000)
        doFetch(symbol, url, retries - 1)
      case e: Exception =>
        logger.error(s"[$symbol] Failed after all retries: ${e.getMessage}")
        List.empty
    }

  private def parseBody(symbol: String, body: String): List[OhlcvRow] =
    try {
      val json   = parse(body)
      val result = (json \ "chart" \ "result")(0)
      if (result == JNothing) {
        logger.warn(s"[$symbol] Empty result returned by Yahoo Finance")
        return List.empty
      }

      val timestamps = (result \ "timestamp").extractOpt[List[Long]].getOrElse(List.empty)
      if (timestamps.isEmpty) return List.empty

      val quote      = (result \ "indicators" \ "quote")(0)
      val adjClose   = (result \ "indicators" \ "adjclose")(0) \ "adjclose"
      val opens      = optDoubles(quote \ "open")
      val highs      = optDoubles(quote \ "high")
      val lows       = optDoubles(quote \ "low")
      val closes     = optDoubles(quote \ "close")
      val adjCloses  = optDoubles(adjClose)
      val volumes    = optLongs(quote \ "volume")

      val divByDate   = parseDividends(result \ "events" \ "dividends")
      val splitByDate = parseSplits(result \ "events" \ "splits")
      val now         = new Timestamp(System.currentTimeMillis())

      timestamps.indices.flatMap { i =>
        val o = at(opens, i);  val h = at(highs, i)
        val l = at(lows, i);   val c = at(closes, i)
        if (o.isEmpty && h.isEmpty && l.isEmpty && c.isEmpty) None
        else {
          val date = Instant.ofEpochSecond(timestamps(i)).atZone(ZoneOffset.UTC).toLocalDate
          Some(OhlcvRow(
            symbol       = symbol,
            trade_date   = Date.valueOf(date),
            open         = o,
            high         = h,
            low          = l,
            close        = c,
            adj_close    = at(adjCloses, i),
            volume       = at(volumes, i),
            dividends    = divByDate.getOrElse(date, 0.0),
            stock_splits = splitByDate.getOrElse(date, 0.0),
            source       = SOURCE,
            ingested_at  = now
          ))
        }
      }.toList
    } catch {
      case e: Exception =>
        logger.error(s"[$symbol] JSON parse error: ${e.getMessage}", e)
        List.empty
    }

  private def at[A](xs: List[A], i: Int): A = if (i < xs.length) xs(i) else xs.headOption.map(_ => null.asInstanceOf[A]).getOrElse(null.asInstanceOf[A])

  private def optDoubles(jv: JValue): List[Option[Double]] = jv match {
    case JArray(arr) => arr.map {
      case JDouble(d) => Some(d)
      case JInt(i)    => Some(i.toDouble)
      case _          => None
    }
    case _ => List.empty
  }

  private def optLongs(jv: JValue): List[Option[Long]] = jv match {
    case JArray(arr) => arr.map {
      case JInt(i)    => Some(i.toLong)
      case JDouble(d) => Some(d.toLong)
      case _          => None
    }
    case _ => List.empty
  }

  private def parseDividends(jv: JValue): Map[LocalDate, Double] = jv match {
    case JObject(fields) =>
      fields.flatMap { case (_, v) =>
        for {
          amount <- (v \ "amount").extractOpt[Double]
          ts     <- (v \ "date").extractOpt[Long]
        } yield Instant.ofEpochSecond(ts).atZone(ZoneOffset.UTC).toLocalDate -> amount
      }.toMap
    case _ => Map.empty
  }

  private def parseSplits(jv: JValue): Map[LocalDate, Double] = jv match {
    case JObject(fields) =>
      fields.flatMap { case (_, v) =>
        for {
          num <- (v \ "numerator").extractOpt[Double]
          den <- (v \ "denominator").extractOpt[Double]
          ts  <- (v \ "date").extractOpt[Long]
          if den != 0.0
        } yield Instant.ofEpochSecond(ts).atZone(ZoneOffset.UTC).toLocalDate -> (num / den)
      }.toMap
    case _ => Map.empty
  }
}
