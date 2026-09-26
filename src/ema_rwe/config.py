import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_cache_path, user_data_path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DATA = Path(__file__).resolve().parent / "data"
SEEDED_FILES = ("ema.sqlite3", "ema-medicines.json")


def default_data_dir() -> Path:
    """<checkout>/data inside a checkout; otherwise the user data dir, seeded once from the wheel's bundled copy."""
    if (REPO_ROOT / "pyproject.toml").is_file():
        return REPO_ROOT / "data"
    target = user_data_path("ema-rwe-mcp")
    target.mkdir(parents=True, exist_ok=True)
    for name in SEEDED_FILES:
        source, destination = BUNDLED_DATA / name, target / name
        if source.is_file() and not destination.exists():
            shutil.copyfile(source, destination)
    return target


def default_db_path() -> Path:
    return default_data_dir() / "ema.sqlite3"


def default_dictionary_dir() -> Path:
    """User concept dictionaries (*.json), read only when present. Nothing ships here: by default the
    MCP client translates questions into English terms itself."""
    return default_data_dir() / "dictionaries"


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("EMA_DB_PATH") or default_db_path()))
    cache_dir: Path = field(
        default_factory=lambda: Path(os.getenv("EMA_CACHE_DIR", str(user_cache_path("ema-rwe-mcp") / "http")))
    )
    interval: float = field(
        default_factory=lambda: max(2.0, float(os.getenv("EMA_REQUEST_INTERVAL_SECONDS", "2")))
    )
    timeout: float = field(default_factory=lambda: float(os.getenv("EMA_HTTP_TIMEOUT_SECONDS", "30")))
    ttl: int = field(default_factory=lambda: int(os.getenv("EMA_CACHE_TTL_SECONDS", "2592000")))
    catalogue_ttl: int = field(default_factory=lambda: int(os.getenv("EMA_CATALOGUE_TTL_SECONDS", "2592000")))
    max_screening_studies: int = field(
        default_factory=lambda: int(os.getenv("EMA_MAX_SCREENING_STUDIES", "5"))
    )
    max_comparison_studies: int = field(
        default_factory=lambda: int(os.getenv("EMA_MAX_COMPARISON_STUDIES", "5"))
    )
    max_listed_candidates: int = field(
        default_factory=lambda: int(os.getenv("EMA_MAX_LISTED_CANDIDATES", "50"))
    )
    user_agent: str = field(default_factory=lambda: os.getenv("EMA_USER_AGENT", "ema-rwe-mcp/0.1"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_backend: str = field(default_factory=lambda: os.getenv("LLM_BACKEND", "compatible"))
    llm_api_version: str = field(default_factory=lambda: os.getenv("LLM_API_VERSION", ""))
    llm_max_steps: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_STEPS", "8")))
    llm_reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", ""))
    # Completion-token ceiling of the configured model; 0 = ask LiteLLM's model table, else a safe default.
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "0")))
    # Server-side extraction: characters of protocol text per provider call, parallel calls, and how long
    # analyze_protocol waits before returning status=extracting for the caller to poll.
    llm_batch_chars: int = field(default_factory=lambda: int(os.getenv("LLM_BATCH_CHARS", "300000")))
    llm_concurrency: int = field(default_factory=lambda: int(os.getenv("LLM_CONCURRENCY", "4")))
    llm_wait_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_WAIT_SECONDS", "120")))
    # Caller-facing response budgets (characters of section text) for the no-key exploration route.
    research_budget_chars: int = field(
        default_factory=lambda: int(os.getenv("EMA_RESEARCH_BUDGET_CHARS", "40000"))
    )
    search_budget_chars: int = field(
        default_factory=lambda: int(os.getenv("EMA_SEARCH_BUDGET_CHARS", "20000"))
    )
    protocol_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["EMA_PROTOCOL_DIR"]) if os.getenv("EMA_PROTOCOL_DIR") else None
        )
    )
    unmatched_log_path: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["EMA_UNMATCHED_LOG_PATH"]) if os.getenv("EMA_UNMATCHED_LOG_PATH") else None
        )
    )
    import_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["EMA_IMPORT_DIR"]) if os.getenv("EMA_IMPORT_DIR") else None
    )

    def __post_init__(self):
        for name in ("max_screening_studies", "max_comparison_studies", "max_listed_candidates"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
                raise ValueError(f"{name} must be an integer from 1 to 1000.")
