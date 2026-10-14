from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: PostgresDsn

    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    embed_device: str = "cuda:0"
    embed_batch_size: int = Field(default=8, gt=0)

    llm_base_url: str = "http://localhost:11435/v1"
    llm_model: str = "qwen2.5:7b"
    llm_timeout_s: float = Field(default=120.0, gt=0)

    max_upload_mb: int = Field(default=10, gt=0)
    max_question_chars: int = Field(default=20000, gt=0)

    api_keys: str = ""
    rate_limit_per_minute: int = Field(default=30, gt=0)

    searxng_url: str = "http://localhost:8402"

    langfuse_host: str = "http://localhost:8330"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=150, ge=0)
    top_k: int = Field(default=5, gt=0)
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_candidates: int = Field(default=20, gt=0)
    rerank_batch_size: int = Field(default=16, gt=0)
    max_context_chars: int = Field(default=12000, gt=0)

    memory_salt: str = "kzbank-local-dev-salt"

    log_level: str = "INFO"
    log_max_mb: int = Field(default=20, gt=0)
    log_backup_count: int = Field(default=4, ge=0)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8020, gt=0, lt=65536)

    raw_data_dir: Path = Path("data/raw")
    log_dir_name: Path = Path("logs")

    def model_post_init(self, _context: object) -> None:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {sorted(valid_levels)}, got {self.log_level!r}")

        if self.rerank_candidates < self.top_k:
            raise ValueError(
                f"rerank_candidates ({self.rerank_candidates}) must be at least "
                f"top_k ({self.top_k}) — reranking cannot invent results."
            )

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size}), otherwise chunking never advances."
            )

    @property
    def log_dir(self) -> Path:
        if self.log_dir_name.is_absolute():
            return self.log_dir_name
        return PROJECT_ROOT / self.log_dir_name

    @property
    def raw_dir(self) -> Path:
        if self.raw_data_dir.is_absolute():
            return self.raw_data_dir
        return PROJECT_ROOT / self.raw_data_dir

    @property
    def processed_dir(self) -> Path:
        return self.raw_dir.parent / "processed"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
