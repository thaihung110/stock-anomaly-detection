package com.stockanomalydetection.synccustomalerts.pipeline

import com.stockanomalydetection.synccustomalerts.config.AppConfig
import org.apache.logging.log4j.LogManager
import org.apache.spark.sql.functions._
import org.apache.spark.sql.{DataFrame, SparkSession}

import java.sql.{DriverManager, Timestamp}
import java.util.Properties

object SyncCustomAlertsPipeline {
  private val logger = LogManager.getLogger(getClass)

  private val MAX_CONNECT_RETRY = 3
  private val CONNECT_RETRY_DELAY_MS = 2000L

  // Postgres may briefly refuse connections right after the earlier Spark
  // apps in the same DAG run finish (connection pool cold start); retry a
  // few times instead of failing the whole job on one transient blip.
  private def newConnection(cfg: AppConfig, retries: Int = MAX_CONNECT_RETRY): java.sql.Connection =
    try {
      val conn = DriverManager.getConnection(cfg.jdbcUrl, cfg.pgUser, cfg.pgPassword)
      // Ensure all timestamp comparisons use UTC
      val stmt = conn.createStatement()
      stmt.execute("SET TIME ZONE 'UTC'")
      stmt.close()
      conn
    } catch {
      case e: Exception if retries > 0 =>
        logger.warn(s"Postgres connection failed: ${e.getMessage}, retrying ($retries left)")
        Thread.sleep(CONNECT_RETRY_DELAY_MS)
        newConnection(cfg, retries - 1)
    }

  def readWatermark(spark: SparkSession, cfg: AppConfig): Timestamp = {
    val conn = newConnection(cfg)
    try {
      val ps = conn.prepareStatement(
        "SELECT last_sync_at FROM sync_watermarks WHERE job_name = ?"
      )
      ps.setString(1, cfg.watermarkJobName)
      val rs = ps.executeQuery()
      val ts = if (rs.next()) {
        val rawTs = rs.getTimestamp(1)
        if (rawTs != null) rawTs else new Timestamp(0L)
      } else {
        new Timestamp(0L)
      }
      rs.close()
      ps.close()
      logger.info(s"Watermark for ${cfg.watermarkJobName}: $ts")
      ts
    } finally {
      conn.close()
    }
  }

  def readNewEvents(spark: SparkSession, cfg: AppConfig, since: Timestamp): DataFrame = {
    val connProps = new Properties()
    connProps.put("user", cfg.pgUser)
    connProps.put("password", cfg.pgPassword)
    connProps.put("driver", "org.postgresql.Driver")

    logger.info(s"Reading user_alert_events since $since")
    // Phase 3: include user_id so each custom-alert row in fact_alert_history
    // carries its owner — required by the new `user_id` column added in the
    // Iceberg ALTER (see docs/backend-redesign-plan.md Phase 3).
    spark.read
      .jdbc(cfg.jdbcUrl, "user_alert_events", connProps)
      .select(
        "event_id", "user_id", "symbol", "triggered_at",
        "field_snapshot", "operator_snapshot", "threshold_snapshot", "triggered_value"
      )
      .filter(col("triggered_at") > lit(since))
      .orderBy(col("triggered_at"))
  }

  def transform(events: DataFrame): DataFrame =
    events.select(
      col("event_id").cast("string").as("alert_id"),
      col("symbol"),
      date_format(col("triggered_at"), "yyyy-MM-dd'T'HH:mm:ss'Z'").as("event_ts"),
      concat_ws(
        " ",
        col("field_snapshot"),
        col("operator_snapshot"),
        col("threshold_snapshot").cast("string")
      ).as("rule_name"),
      lit("INFO").as("severity"),
      col("triggered_value").cast("double"),
      col("threshold_snapshot").cast("double").as("threshold"),
      lit("user_custom").as("alert_source"),
      date_format(current_timestamp(), "yyyy-MM-dd'T'HH:mm:ss'Z'").as("written_at"),
      col("user_id").cast("string").as("user_id")
    )

  def writeToIceberg(df: DataFrame, cfg: AppConfig): Unit = {
    logger.info(s"Appending rows to ${cfg.factTable}")
    df.writeTo(cfg.factTable).append()
    logger.info(s"Iceberg append committed to ${cfg.factTable}")
  }

  def updateWatermark(spark: SparkSession, cfg: AppConfig, newWatermark: Timestamp): Unit = {
    val conn = newConnection(cfg)
    try {
      val ps = conn.prepareStatement(
        """INSERT INTO sync_watermarks (job_name, last_sync_at)
          |VALUES (?, ?)
          |ON CONFLICT (job_name) DO UPDATE SET last_sync_at = EXCLUDED.last_sync_at
          |""".stripMargin
      )
      ps.setString(1, cfg.watermarkJobName)
      ps.setTimestamp(2, newWatermark)
      ps.executeUpdate()
      ps.close()
      logger.info(s"Watermark for ${cfg.watermarkJobName} updated to $newWatermark")
    } finally {
      conn.close()
    }
  }

  def run(spark: SparkSession, cfg: AppConfig): Unit = {
    val since  = readWatermark(spark, cfg)
    val events = readNewEvents(spark, cfg, since).cache()
    try {
      val stats    = events
        .agg(count("*").as("cnt"), max("triggered_at").as("max_ts"))
        .collect()(0)
      val rowCount = stats.getLong(0)

      if (rowCount > 0) {
        val newWatermark = stats.getTimestamp(1)

        // Invariant: updateWatermark is called only after writeToIceberg commits.
        // If writeToIceberg throws, watermark stays at old value → safe retry.
        // If updateWatermark throws after a successful write, rows will be re-synced
        // on the next run (Iceberg append → duplicates); this must be investigated manually.
        try {
          writeToIceberg(transform(events), cfg)
        } catch {
          case e: Exception =>
            logger.error(s"Iceberg write failed for ${cfg.factTable}: ${e.getMessage}. Watermark NOT updated.")
            throw e
        }

        try {
          updateWatermark(spark, cfg, newWatermark)
        } catch {
          case e: Exception =>
            logger.error(
              s"Iceberg write committed but watermark update failed. " +
              s"Next run will re-sync events since $since. " +
              s"Manual dedup of ${cfg.factTable} may be required."
            )
            throw e
        }

        logger.info(s"Synced $rowCount custom alert events, new watermark: $newWatermark")
      } else {
        logger.info(s"No new custom alert events since $since — skipping write")
      }
    } finally {
      events.unpersist()
    }
  }
}
