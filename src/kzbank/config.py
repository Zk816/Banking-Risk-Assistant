"""Settings for the whole project, loaded once from .env.

This is the only module allowed to read environment variables. Everything else
imports `settings` from here, so there is exactly one place to look when you
wonder "where does this number come from?".
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Validated configuration. Bad values fail loudly at import, not mid-run."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    database_url: PostgresDsn

    # --- Embeddings ---
    embed_model: str = "BAAI/bge-m3"
    embed_dim: int = 1024
    # "cuda:0" is the first *visible* GPU, which CUDA_VISIBLE_DEVICES selects.
    embed_device: str = "cuda:0"
    embed_batch_size: int = Field(default=8, gt=0)

    # --- Generation ---
    llm_base_url: str = "http://localhost:11435/v1"
    llm_model: str = "qwen2.5:7b"
    llm_timeout_s: float = Field(default=120.0, gt=0)

    # --- Retrieval ---
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=150, ge=0)
    top_k: int = Field(default=5, gt=0)
    # How many candidates the cross-encoder re-scores. Retrieval is cheap and
    # reranking is not, so go wide here and narrow to top_k after.
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_candidates: int = Field(default=20, gt=0)
    rerank_batch_size: int = Field(default=16, gt=0)
    max_context_chars: int = Field(default=12000, gt=0)

    # --- Logging ---
    # Hard ceiling on disk: log_max_mb * (log_backup_count + 1). Defaults to 100 MB.
    # Salt for pseudonymising user ids in long-term memory. The default is for
    # local runs only; set MEMORY_SALT in .env anywhere real users exist.
    memory_salt: str = "kzbank-local-dev-salt"

    log_level: str = "INFO"
    log_max_mb: int = Field(default=20, gt=0)
    log_backup_count: int = Field(default=4, ge=0)

    # --- API ---
    # 127.0.0.1 by default: this service has no auth yet, and binding 0.0.0.0
    # would expose it to the network.
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8020, gt=0, lt=65536)

    # --- Paths ---
    raw_data_dir: Path = Path("data/raw")
    log_dir_name: Path = Path("logs")

    def model_post_init(self, _context: object) -> None:
        """Catch the config mistakes that produce silent nonsense downstream."""
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
        """Absolute path to the log directory."""
        if self.log_dir_name.is_absolute():
            return self.log_dir_name
        return PROJECT_ROOT / self.log_dir_name

    @property
    def raw_dir(self) -> Path:
        """Absolute path to raw data, so scripts work from any working directory."""
        if self.raw_data_dir.is_absolute():
            return self.raw_data_dir
        return PROJECT_ROOT / self.raw_data_dir


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()  # type: ignore[call-arg]  # values come from .env


settings = get_settings()
