from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8"
    )

    finnhub_api_key: str
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "raw.stock.news"
    kafka_compression_type: str = "gzip"

    symbols: str = (
        "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,JPM,V,UNH,"
        "XOM,WMT,MA,JNJ,PG,HD,CVX,MRK,ABBV,LLY,"
        "PEP,KO,AVGO,COST,TMO,MCD,CSCO,ABT,ACN,DHR,"
        "NEE,NKE,WFC,QCOM,TXN,PM,BMY,CRM,HON,AMGN,"
        "RTX,LOW,SPGI,LIN,INTU,AMAT,GS,BLK,CAT,AXP"
    )

    # Poll every 5 minutes
    poll_interval_sec: float = 300.0
    # Delay between per-symbol requests to stay under 60 req/min
    request_delay_sec: float = 1.1
    # How many calendar days back to fetch news per poll
    lookback_days: int = 2
    # Max size of in-memory dedup set before it is cleared
    dedup_max_size: int = 10_000

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]
