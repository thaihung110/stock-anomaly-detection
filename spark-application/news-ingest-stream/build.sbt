ThisBuild / organization := "com.stockanomalydetection"
ThisBuild / version      := "1.0.0"
ThisBuild / scalaVersion := "2.12.18"

val sparkVersion = "3.5.1"

lazy val root = (project in file("."))
  .settings(
    name := "news-ingest-stream",
    libraryDependencies ++= Seq(
      // NOT marked "provided" so Metals/BSP includes them in the IDE classpath.
      // They are excluded from the fat-jar via assemblyExcludedJars below.
      "org.apache.spark"   %% "spark-core"                % sparkVersion,
      "org.apache.spark"   %% "spark-sql"                 % sparkVersion,
      "org.apache.spark"   %% "spark-sql-kafka-0-10"      % sparkVersion,
      "org.apache.iceberg" %% "iceberg-spark-runtime-3.5" % "1.5.2",
      "org.apache.hadoop"   % "hadoop-aws"                % "3.3.4",
      "com.amazonaws"       % "aws-java-sdk-bundle"       % "1.12.262"
    ),
    assembly / assemblyJarName := s"${name.value}-assembly-${version.value}.jar",
    // Exclude Spark core/sql from fat-jar — they are provided by the cluster.
    assembly / assemblyExcludedJars := {
      (assembly / fullClasspath).value.filter { jar =>
        val n = jar.data.getName
        n.startsWith("spark-core_")        ||
        n.startsWith("spark-sql_")         ||
        n.startsWith("spark-catalyst_")    ||
        n.startsWith("spark-network-")     ||
        n.startsWith("spark-unsafe_")      ||
        n.startsWith("spark-launcher_")    ||
        n.startsWith("spark-tags_")        ||
        n.startsWith("spark-sketch_")      ||
        n.startsWith("scala-library-")     ||
        n.startsWith("scala-reflect-")
      }
    },
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
