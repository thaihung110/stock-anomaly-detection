package com.stockanomalydetection.synccustomalerts.pipeline

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.types._
import org.scalatest.BeforeAndAfterAll
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

import java.sql.Timestamp
import java.time.Instant

class SyncCustomAlertsPipelineTransformSpec extends AnyFlatSpec with Matchers with BeforeAndAfterAll {

  private var spark: SparkSession = _

  override def beforeAll(): Unit = {
    spark = SparkSession.builder()
      .appName("SyncCustomAlertsPipelineTransformSpec")
      .master("local[1]")
      .config("spark.sql.session.timeZone", "UTC")
      .config("spark.ui.enabled", "false")
      .getOrCreate()
  }

  override def afterAll(): Unit = {
    if (spark != null) spark.stop()
  }

  private val fixedTs = Timestamp.from(Instant.parse("2024-06-01T10:30:00Z"))

  private def makeEventsDF() = {
    import spark.implicits._
    Seq(
      ("uuid-1", "user-A", "AAPL", fixedTs, "price",         ">",          150.0, 155.5),
      ("uuid-2", "user-B", "TSLA", fixedTs, "volume_zscore", "CROSSES_UP", 3.0,   3.8)
    ).toDF("event_id", "user_id", "symbol", "triggered_at",
           "field_snapshot", "operator_snapshot", "threshold_snapshot", "triggered_value")
  }

  "transform" should "set alert_source to 'user_custom' for every row" in {
    val result  = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val sources = result.select("alert_source").as[String](org.apache.spark.sql.Encoders.STRING).collect().toSet
    sources shouldBe Set("user_custom")
  }

  it should "set severity to 'INFO' for every row" in {
    val result     = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val severities = result.select("severity").as[String](org.apache.spark.sql.Encoders.STRING).collect().toSet
    severities shouldBe Set("INFO")
  }

  it should "map event_id to alert_id preserving the UUID value" in {
    val result   = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val alertIds = result.select("alert_id").as[String](org.apache.spark.sql.Encoders.STRING).collect()
    alertIds should contain("uuid-1")
    alertIds should contain("uuid-2")
  }

  it should "preserve the symbol column unchanged" in {
    val result  = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val symbols = result.select("symbol").as[String](org.apache.spark.sql.Encoders.STRING).collect().toSet
    symbols shouldBe Set("AAPL", "TSLA")
  }

  it should "format event_ts as ISO-8601 with Z suffix" in {
    val result  = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val eventTs = result.select("event_ts").as[String](org.apache.spark.sql.Encoders.STRING).collect()
    eventTs.forall(ts => ts.matches("""\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z""")) shouldBe true
  }

  it should "construct rule_name by concatenating field, operator, and threshold" in {
    val result    = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val ruleNames = result
      .orderBy("symbol")
      .select("rule_name")
      .as[String](org.apache.spark.sql.Encoders.STRING)
      .collect()

    ruleNames(0) should include("price")
    ruleNames(0) should include(">")
    ruleNames(0) should include("150.0")

    ruleNames(1) should include("volume_zscore")
    ruleNames(1) should include("CROSSES_UP")
    ruleNames(1) should include("3.0")
  }

  it should "map threshold_snapshot to threshold column" in {
    val result     = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val thresholds = result
      .orderBy("symbol")
      .select("threshold")
      .as[Double](org.apache.spark.sql.Encoders.scalaDouble)
      .collect()

    thresholds(0) shouldBe 150.0
    thresholds(1) shouldBe 3.0
  }

  it should "preserve triggered_value as a double" in {
    val result = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val values = result
      .orderBy("symbol")
      .select("triggered_value")
      .as[Double](org.apache.spark.sql.Encoders.scalaDouble)
      .collect()

    values(0) shouldBe 155.5
    values(1) shouldBe 3.8
  }

  it should "produce exactly 10 output columns with correct names" in {
    val result   = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val expected = Set(
      "alert_id", "symbol", "event_ts", "rule_name",
      "severity", "triggered_value", "threshold", "alert_source", "written_at",
      "user_id"
    )
    result.columns.toSet shouldBe expected
  }

  it should "preserve user_id as a string column" in {
    val result = SyncCustomAlertsPipeline.transform(makeEventsDF())
    val userIds = result
      .orderBy("symbol")
      .select("user_id")
      .as[String](org.apache.spark.sql.Encoders.STRING)
      .collect()
      .toSet
    userIds shouldBe Set("user-A", "user-B")
  }

  it should "not expose raw source columns in output" in {
    val result = SyncCustomAlertsPipeline.transform(makeEventsDF())
    result.columns should not contain "event_id"
    result.columns should not contain "triggered_at"
    result.columns should not contain "field_snapshot"
    result.columns should not contain "operator_snapshot"
    result.columns should not contain "threshold_snapshot"
  }

  it should "handle an empty input DataFrame without error" in {
    import spark.implicits._
    val empty = Seq.empty[(String, String, String, Timestamp, String, String, Double, Double)]
      .toDF("event_id", "user_id", "symbol", "triggered_at",
            "field_snapshot", "operator_snapshot", "threshold_snapshot", "triggered_value")

    val result = SyncCustomAlertsPipeline.transform(empty)
    result.count() shouldBe 0
    result.columns.length shouldBe 10
  }
}
