import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_cache_path, user_data_path


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("EMA_DB_PATH", str(user_data_path("ema-rwe-mcp") / "ema.sqlite3"))
        )
    )
    cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("EMA_CACHE_DIR", str(user_cache_path("ema-rwe-mcp") / "http")))
    )
    interval: float = field(
        default_factory=lambda: max(2.0, float(os.getenv("EMA_REQUEST_INTERVAL_SECONDS", "2")))
    )
    timeout: float = field(default_factory=lambda: float(os.getenv("EMA_HTTP_TIMEOUT_SECONDS", "30")))
    ttl: int = field(default_factory=lambda: int(os.getenv("EMA_CACHE_TTL_SECONDS", "604800")))
    user_agent: str = field(default_factory=lambda: os.getenv("EMA_USER_AGENT", "ema-rwe-mcp/0.1"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_backend: str = field(default_factory=lambda: os.getenv("LLM_BACKEND", "compatible"))
    llm_api_version: str = field(default_factory=lambda: os.getenv("LLM_API_VERSION", ""))
    llm_max_steps: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_STEPS", "8")))
    protocol_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["EMA_PROTOCOL_DIR"]) if os.getenv("EMA_PROTOCOL_DIR") else None
        )
    )
