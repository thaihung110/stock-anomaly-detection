package com.stockanomalydetection.newsingest.config

import org.apache.spark.sql.SparkSession

object CatalogConfigurator {
  def configure(spark: SparkSession): Unit = {
    val gravitinoUri = sys.env("GRAVITINO_URI")
    val warehouse = sys.env("ICEBERG_WAREHOUSE")
    val minioEndpoint = sys.env("MINIO_ENDPOINT")
    val minioAccess = sys.env("MINIO_ACCESS_KEY")
    val minioSecret = sys.env("MINIO_SECRET_KEY")

    spark.conf.set("spark.sql.catalog.gravitino_catalog", "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.type", "rest")
    spark.conf.set("spark.sql.catalog.gravitino_catalog.uri", gravitinoUri)
    spark.conf.set("spark.sql.catalog.gravitino_catalog.warehouse", warehouse)

    spark.conf.set("spark.hadoop.fs.s3a.endpoint", minioEndpoint)
    spark.conf.set("spark.hadoop.fs.s3a.access.key", minioAccess)
    spark.conf.set("spark.hadoop.fs.s3a.secret.key", minioSecret)
    spark.conf.set("spark.hadoop.fs.s3a.path.style.access", "true")
    spark.conf.set("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
  }
}
