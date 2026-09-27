"""Mini Jarvis - unified config loader.

One cached, typed place that reads config.json. Every module calls
config.load().<field> instead of opening the file itself; Phases 7-9 add
their keys to the Config dataclass here and nowhere else.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass
class Config:
    poll_interval: float = 0.3
    confidence_threshold: float = 0.6
    planner_model: str = "llama3:latest"
    search_paths: list[str] = field(default_factory=list)
    memory_vault_path: str = ""
    memory_db_path: str = ""
    embedding_model: str = "nomic-embed-text"
    memory_synthesize: bool = True
    memory_log_activity: bool = False
    reminder_poll_seconds: int = 30
    reminder_startup_grace_minutes: int = 10


def _as_float(raw: dict, key: str, default: float) -> float:
    try:
        return float(raw.get(key, default))
    except (TypeError, ValueError):
        return default


def _as_str(raw: dict, key: str, default: str) -> str:
    value = raw.get(key, default)
    return value if isinstance(value, str) and value else default


def _as_str_list(raw: dict, key: str) -> list[str]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_bool(raw: dict, key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(raw: dict, key: str, default: int) -> int:
    try:
        return int(raw.get(key, default))
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def load() -> Config:
    """Parse config.json once per process; bad or missing keys use defaults."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    return Config(
        poll_interval=_as_float(raw, "poll_interval", 0.3),
        confidence_threshold=_as_float(raw, "confidence_threshold", 0.6),
        planner_model=_as_str(raw, "planner_model", "llama3:latest"),
        search_paths=_as_str_list(raw, "search_paths"),
        memory_vault_path=_as_str(raw, "memory_vault_path", ""),
        memory_db_path=_as_str(raw, "memory_db_path", ""),
        embedding_model=_as_str(raw, "embedding_model", "nomic-embed-text"),
        memory_synthesize=_as_bool(raw, "memory_synthesize", True),
        memory_log_activity=_as_bool(raw, "memory_log_activity", False),
        reminder_poll_seconds=_as_int(raw, "reminder_poll_seconds", 30),
        reminder_startup_grace_minutes=_as_int(raw, "reminder_startup_grace_minutes", 10),
    )
