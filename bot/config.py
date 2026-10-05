"""Load host-side bot settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def project_root() -> Path:
    """Return the WeJamBot repository root."""
    return _ROOT


def _env(name: str, default: str = "") -> str:
    """Read a trimmed environment variable with a default."""
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a fallback."""
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable; blank values use the default."""
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the host-side WeChat chat bot."""

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    memory_max_turns: int
    data_dir: Path
    reply_policy_path: Path
    personas_dir: Path
    personas_index_path: Path
    allow_chats_path: Path
    wejam_target: str
    bot_name: str


def load_settings() -> Settings:
    """Build Settings from process environment (and optional .env)."""
    data_dir = Path(_env("BOT_DATA_DIR") or str(_ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = Path(_env("PERSONAS_DIR") or str(data_dir / "personas"))
    personas_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL", "deepseek-flash"),
        llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 25.0),
        memory_max_turns=_env_int("MEMORY_MAX_TURNS", 12),
        data_dir=data_dir,
        reply_policy_path=Path(
            _env("REPLY_POLICY_PATH") or str(_ROOT / "reply_policy.toml")
        ),
        personas_dir=personas_dir,
        personas_index_path=Path(
            _env("PERSONAS_INDEX_PATH") or str(_ROOT / "personas.toml")
        ),
        allow_chats_path=Path(
            _env("ALLOW_CHATS_PATH") or str(data_dir / "allow_chats.txt")
        ),
        wejam_target=_env("WEJAM_TARGET", "127.0.0.1:7700"),
        bot_name=_env("BOT_NAME", "bot"),
    )
