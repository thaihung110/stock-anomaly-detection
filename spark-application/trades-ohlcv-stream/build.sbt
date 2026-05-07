ThisBuild / organization := "com.stockanomalydetection"
ThisBuild / version      := "1.0.0"
ThisBuild / scalaVersion := "2.12.18"

val sparkVersion   = "3.5.1"
val icebergVersion = "1.10.1"

lazy val root = (project in file("."))
  .settings(
    name := "trades-ohlcv-stream",

    libraryDependencies ++= Seq(
      // Spark — provided by the cluster; excluded from the fat jar
      "org.apache.spark"   %% "spark-core"                     % sparkVersion   % "provided",
      "org.apache.spark"   %% "spark-sql"                      % sparkVersion   % "provided",

      // Kafka connector — must be bundled (not in base Spark image)
      "org.apache.spark"   %% "spark-sql-kafka-0-10"           % sparkVersion,

      // Iceberg runtime + AWS bundle — shaded, must be bundled
      "org.apache.iceberg"  % "iceberg-spark-runtime-3.5_2.12" % icebergVersion,
      "org.apache.iceberg"  % "iceberg-aws-bundle"             % icebergVersion,

      // Hadoop S3A — bundled (not present in base Spark image)
      "org.apache.hadoop"   % "hadoop-aws"                     % "3.3.4"
    ),

    // ---- fat-jar settings ----
    assembly / assemblyJarName := s"${name.value}-assembly-${version.value}.jar",

    assembly / assemblyMergeStrategy := {
      case PathList("META-INF", "services", _*) => MergeStrategy.concat
      case PathList("META-INF", _*)             => MergeStrategy.discard
      case "reference.conf"                     => MergeStrategy.concat
      case "application.conf"                   => MergeStrategy.concat
      case _                                    => MergeStrategy.first
    },

    // Scala stdlib is provided by the cluster
    assembly / assemblyOption :=
      (assembly / assemblyOption).value.withIncludeScala(false)
  )
