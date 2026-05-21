ThisBuild / organization := "com.stockanomalydetection"
ThisBuild / version      := "1.0.0"
ThisBuild / scalaVersion := "2.12.18"

val sparkVersion   = "3.5.1"
val icebergVersion = "1.10.1"

lazy val root = (project in file("."))
  .settings(
    name := "sync-custom-alerts",

    libraryDependencies ++= Seq(
      "org.apache.spark"   %% "spark-core"                     % sparkVersion   % "provided",
      "org.apache.spark"   %% "spark-sql"                      % sparkVersion   % "provided",

      "org.apache.iceberg"  % "iceberg-spark-runtime-3.5_2.12" % icebergVersion,
      "org.apache.iceberg"  % "iceberg-aws-bundle"             % icebergVersion,

      "org.apache.hadoop"   % "hadoop-aws"                     % "3.3.4",

      "org.postgresql"      % "postgresql"                     % "42.7.3",

      // Test dependencies — Spark available in test scope (overrides provided)
      "org.apache.spark"   %% "spark-core"                     % sparkVersion   % "test",
      "org.apache.spark"   %% "spark-sql"                      % sparkVersion   % "test",
      "org.scalatest"      %% "scalatest"                      % "3.2.17"       % "test"
    ),

    assembly / assemblyJarName := s"${name.value}-assembly-${version.value}.jar",

    assembly / assemblyMergeStrategy := {
      case PathList("META-INF", "services", _*) => MergeStrategy.concat
      case PathList("META-INF", _*)             => MergeStrategy.discard
      case "reference.conf"                     => MergeStrategy.concat
      case "application.conf"                   => MergeStrategy.concat
      case _                                    => MergeStrategy.first
    },

    assembly / assemblyOption :=
      (assembly / assemblyOption).value.withIncludeScala(false)
  )
