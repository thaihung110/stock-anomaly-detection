package com.stockanomalydetection.ohlcvloader.schema

import java.sql.{Date, Timestamp}

case class OhlcvRow(
  symbol:       String,
  trade_date:   Date,
  open:         Option[Double],
  high:         Option[Double],
  low:          Option[Double],
  close:        Option[Double],
  adj_close:    Option[Double],
  volume:       Option[Long],
  dividends:    Double,
  stock_splits: Double,
  source:       String,
  ingested_at:  Timestamp
)
