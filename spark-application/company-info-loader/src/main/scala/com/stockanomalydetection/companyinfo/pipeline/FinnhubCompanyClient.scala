package com.stockanomalydetection.companyinfo.pipeline

import com.stockanomalydetection.companyinfo.schema.CompanyInfoRow
import java.net.URI
import java.net.http.{HttpClient, HttpRequest, HttpResponse}
import java.sql.Timestamp
import java.time.Duration
import org.apache.logging.log4j.LogManager
import org.json4s._
import org.json4s.jackson.JsonMethods._

/**
 * Fetches company metadata from Finnhub using two endpoints per symbol:
 *
 *   1. GET /stock/profile2  → name, exchange, country, currency, website,
 *                              marketCapitalization, shareOutstanding, finnhubIndustry
 *   2. GET /stock/metric    → beta, peBasicExclExtraTTM, 52WeekHigh/Low,
 *                              50DayMA, 200DayMA, dividendYieldIndicatedAnnual
 *
 * Both endpoints require only `?token=API_KEY` — no cookie/crumb auth.
 * Rate limit on free tier: 60 calls/minute → we stay well within that for 50 symbols.
 */
object FinnhubCompanyClient {
  private val logger = LogManager.getLogger(getClass)

  private val http = HttpClient.newBuilder()
    .connectTimeout(Duration.ofSeconds(15))
    .build()

  private val BASE_URL  = "https://finnhub.io/api/v1"
  private val MAX_RETRY = 3

  implicit private val formats: Formats = DefaultFormats

  def fetchInfo(symbol: String, apiKey: String): Option[CompanyInfoRow] = {
    val profileOpt = fetchProfile(symbol, apiKey)
    val metricOpt  = fetchMetric(symbol, apiKey)

    // If profile completely failed, skip symbol — no meaningful row to write.
    profileOpt.map { profile =>
      val metric = metricOpt.getOrElse(Map.empty[String, JValue])
      buildRow(symbol, profile, metric)
    }
  }

  // ---------------------------------------------------------------------------
  // Profile2: name, exchange, country, currency, website, marketCap, shares, industry
  // ---------------------------------------------------------------------------

  private def fetchProfile(symbol: String, apiKey: String): Option[Map[String, JValue]] = {
    val url = s"$BASE_URL/stock/profile2?symbol=$symbol&token=$apiKey"
    doGet(symbol, url, "profile2", MAX_RETRY).flatMap { body =>
      val json = parse(body)
      // Finnhub returns {} for unknown symbols
      json match {
        case JObject(fields) if fields.nonEmpty => Some(fields.toMap)
        case _ =>
          logger.warn(s"[$symbol] profile2 returned empty object — symbol not found in Finnhub")
          None
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Metric: beta, PE, 52-week hi/lo, 50/200-day MA, dividend yield
  // ---------------------------------------------------------------------------

  private def fetchMetric(symbol: String, apiKey: String): Option[Map[String, JValue]] = {
    val url = s"$BASE_URL/stock/metric?symbol=$symbol&metric=all&token=$apiKey"
    doGet(symbol, url, "metric", MAX_RETRY).map { body =>
      val json = parse(body)
      // metric fields are nested under "metric" key
      (json \ "metric") match {
        case JObject(fields) => fields.toMap
        case _               => Map.empty[String, JValue]
      }
    }
  }

  // ---------------------------------------------------------------------------
  // HTTP helper with retry
  // ---------------------------------------------------------------------------

  private def doGet(symbol: String, url: String, endpoint: String, retries: Int): Option[String] =
    try {
      val req = HttpRequest.newBuilder(URI.create(url))
        .header("User-Agent", "Mozilla/5.0")
        .header("Accept", "application/json")
        .GET()
        .build()
      val resp = http.send(req, HttpResponse.BodyHandlers.ofString())
      resp.statusCode() match {
        case 200 => Some(resp.body())
        case 429 if retries > 0 =>
          logger.warn(s"[$symbol] [$endpoint] Rate-limited (429), waiting 10s ($retries retries left)")
          Thread.sleep(10000)
          doGet(symbol, url, endpoint, retries - 1)
        case code if code >= 500 && retries > 0 =>
          logger.warn(s"[$symbol] [$endpoint] HTTP $code, retrying in 3s ($retries left)")
          Thread.sleep(3000)
          doGet(symbol, url, endpoint, retries - 1)
        case 403 =>
          logger.error(s"[$symbol] [$endpoint] HTTP 403 — check FINNHUB_API_KEY")
          None
        case code =>
          logger.error(s"[$symbol] [$endpoint] HTTP $code after retries")
          None
      }
    } catch {
      case e: Exception if retries > 0 =>
        logger.warn(s"[$symbol] [$endpoint] ${e.getMessage}, retrying in 2s ($retries left)")
        Thread.sleep(2000)
        doGet(symbol, url, endpoint, retries - 1)
      case e: Exception =>
        logger.error(s"[$symbol] [$endpoint] Failed after all retries: ${e.getMessage}")
        None
    }

  // ---------------------------------------------------------------------------
  // Row builder — maps Finnhub JSON fields → CompanyInfoRow
  //
  // Field mapping:
  //   profile2.name                        → short_name (Finnhub has one name field)
  //   profile2.name                        → long_name  (same — Finnhub doesn't split)
  //   profile2.exchange                    → exchange
  //   profile2.finnhubIndustry             → industry   (Finnhub's own classification)
  //   profile2.country                     → country
  //   profile2.currency                    → currency
  //   profile2.weburl                      → website
  //   profile2.marketCapitalization * 1e6  → market_cap (Finnhub stores in millions)
  //   profile2.shareOutstanding * 1e6      → shares_outstanding (in millions)
  //   metric.beta                          → beta
  //   metric.peBasicExclExtraTTM           → trailing_pe
  //   metric.52WeekHigh                    → fifty_two_week_high
  //   metric.52WeekLow                     → fifty_two_week_low
  //   metric.50DayMA                       → fifty_day_average
  //   metric.200DayMA                      → two_hundred_day_avg
  //   metric.dividendYieldIndicatedAnnual  → dividend_yield
  //   forward_pe                           → None (not available on Finnhub free tier)
  //   quote_type                           → None (not available in profile2)
  //   sector                               → None (Finnhub has industry only, not separate sector)
  // ---------------------------------------------------------------------------

  private def buildRow(
    symbol:  String,
    profile: Map[String, JValue],
    metric:  Map[String, JValue]
  ): CompanyInfoRow = {
    val now = new Timestamp(System.currentTimeMillis())

    CompanyInfoRow(
      symbol              = symbol,
      short_name          = str(profile, "name"),
      long_name           = str(profile, "name"),
      exchange            = str(profile, "exchange"),
      quote_type          = None,
      sector              = None,
      industry            = str(profile, "finnhubIndustry").filter(s => s.nonEmpty && s != "N/A"),
      country             = str(profile, "country"),
      currency            = str(profile, "currency"),
      website             = str(profile, "weburl"),
      // Finnhub stores marketCapitalization and shareOutstanding in *millions*
      market_cap          = dbl(profile, "marketCapitalization").map(v => (v * 1000000).toLong),
      beta                = dbl(metric, "beta"),
      trailing_pe         = dbl(metric, "peBasicExclExtraTTM"),
      forward_pe          = None,
      fifty_two_week_high = dbl(metric, "52WeekHigh"),
      fifty_two_week_low  = dbl(metric, "52WeekLow"),
      fifty_day_average   = dbl(metric, "50DayMA"),
      two_hundred_day_avg = dbl(metric, "200DayMA"),
      // Finnhub shareOutstanding is in millions
      shares_outstanding  = dbl(profile, "shareOutstanding").map(v => (v * 1000000).toLong),
      dividend_yield      = dbl(metric, "dividendYieldIndicatedAnnual"),
      fetched_at          = now
    )
  }

  // ---------------------------------------------------------------------------
  // JSON extractors
  // ---------------------------------------------------------------------------

  private def str(m: Map[String, JValue], key: String): Option[String] =
    m.get(key).flatMap {
      case JString(s) if s.nonEmpty => Some(s)
      case _                        => None
    }

  private def dbl(m: Map[String, JValue], key: String): Option[Double] =
    m.get(key).flatMap {
      case JDouble(d) => Some(d)
      case JInt(i)    => Some(i.toDouble)
      case JLong(l)   => Some(l.toDouble)
      case JDecimal(d)=> Some(d.toDouble)
      case _          => None
    }
}
