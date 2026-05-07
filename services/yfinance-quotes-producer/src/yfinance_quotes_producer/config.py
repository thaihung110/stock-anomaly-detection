from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8"
    )

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "raw.stock.quotes"

    symbols: str = (
        "AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,JPM,V,UNH,"
        "XOM,WMT,MA,JNJ,PG,HD,CVX,MRK,ABBV,LLY,"
        "PEP,KO,AVGO,COST,TMO,MCD,CSCO,ABT,ACN,DHR,"
        "NEE,NKE,WFC,QCOM,TXN,PM,BMY,CRM,HON,AMGN,"
        "RTX,LOW,SPGI,LIN,INTU,AMAT,GS,BLK,CAT,AXP"
    )

    reconnect_delay_sec: float = 5.0
    reconnect_max_delay_sec: float = 60.0

    kafka_compression_type: str = "gzip"

    metrics_port: int = 8000

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]
