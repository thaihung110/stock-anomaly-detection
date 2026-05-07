package com.stockanomalydetection.companyinfo.schema

import java.sql.Timestamp

case class CompanyInfoRow(
  symbol:              String,
  short_name:          Option[String],
  long_name:           Option[String],
  exchange:            Option[String],
  quote_type:          Option[String],
  sector:              Option[String],
  industry:            Option[String],
  country:             Option[String],
  currency:            Option[String],
  website:             Option[String],
  market_cap:          Option[Long],
  beta:                Option[Double],
  trailing_pe:         Option[Double],
  forward_pe:          Option[Double],
  fifty_two_week_high: Option[Double],
  fifty_two_week_low:  Option[Double],
  fifty_day_average:   Option[Double],
  two_hundred_day_avg: Option[Double],
  shares_outstanding:  Option[Long],
  dividend_yield:      Option[Double],
  fetched_at:          Timestamp
)
