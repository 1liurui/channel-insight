"""全局配置。所有路径与连接串的唯一来源，不在别处硬编码。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    dashscope_api_key: str = ""
    vlm_model: str = "qwen3-vl-plus"

    pg_dsn: str = "postgresql://localhost:5432/channel_agent"
    redis_url: str = "redis://localhost:6379/0"
    duckdb_path: Path = Path("data/warehouse.duckdb")
    qdrant_path: Path = Path("data/qdrant")

    embed_model: str = "BAAI/bge-small-zh-v1.5"
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_pool: int = 60          # 送进 cross-encoder 的候选池大小

    @property
    def duckdb_file(self) -> Path:
        return ROOT / self.duckdb_path

    @property
    def qdrant_dir(self) -> Path:
        return ROOT / self.qdrant_path

    @property
    def conf_dir(self) -> Path:
        return ROOT / "conf"


@lru_cache
def get_settings() -> Settings:
    return Settings()
