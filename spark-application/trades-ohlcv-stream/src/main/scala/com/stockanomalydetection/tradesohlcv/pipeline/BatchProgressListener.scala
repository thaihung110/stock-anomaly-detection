package com.stockanomalydetection.tradesohlcv.pipeline

import org.apache.spark.sql.streaming.StreamingQueryListener
import org.apache.spark.sql.streaming.StreamingQueryListener.{
  QueryProgressEvent,
  QueryStartedEvent,
  QueryTerminatedEvent
}
import org.slf4j.LoggerFactory

object BatchProgressListener extends StreamingQueryListener {
  private val log = LoggerFactory.getLogger(getClass)

  override def onQueryStarted(event: QueryStartedEvent): Unit =
    log.info(s"[trades-ohlcv-stream] Query started: id=${event.id} name=${event.name}")

  override def onQueryProgress(event: QueryProgressEvent): Unit = {
    val p         = event.progress
    val watermark = Option(p.eventTime.get("watermark")).getOrElse("N/A")
    val offsets   = p.sources.map(_.endOffset).mkString("[", ", ", "]")
    log.info(
      s"[trades-ohlcv-stream] Batch ${p.batchId}: " +
        s"inputRows=${p.numInputRows}, watermark=$watermark, offsets=$offsets"
    )
  }

  override def onQueryTerminated(event: QueryTerminatedEvent): Unit =
    event.exception match {
      case Some(err) => log.error(s"[trades-ohlcv-stream] Query terminated with error: $err")
      case None      => log.info(s"[trades-ohlcv-stream] Query terminated cleanly: id=${event.id}")
    }
}
