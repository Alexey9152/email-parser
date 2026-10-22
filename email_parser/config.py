"""Runtime configuration: .env + CLI overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


@dataclass(slots=True)
class Settings:
    """All knobs in one place. Values come from .env unless overridden."""

    maps_provider: str = "playwright"
    apify_token: str = ""
    apify_actor: str = "compass/crawler-google-places"
    apify_search_actor: str = "apify/google-search-scraper"
    outscraper_key: str = ""
    serpapi_key: str = ""
    search_backend: str = "auto"
    search_delay: float = 4.0
    search_cooldown: float = 120.0
    search_timeout: float = 420.0
    search_max_queries: int = 400

    http_timeout: float = 15.0
    http_workers: int = 8
    headless: bool = True

    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            maps_provider=_env("MAPS_PROVIDER", "playwright").lower(),
            apify_token=_env("APIFY_TOKEN"),
            apify_actor=_env("APIFY_ACTOR", "compass/crawler-google-places"),
            apify_search_actor=_env("APIFY_SEARCH_ACTOR", "apify/google-search-scraper"),
            outscraper_key=_env("OUTSCRAPER_API_KEY"),
            serpapi_key=_env("SERPAPI_KEY"),
            search_backend=_env("SEARCH_BACKEND", "auto").lower(),
            search_delay=_env_float("SEARCH_DELAY", 4.0),
            search_cooldown=_env_float("SEARCH_COOLDOWN", 120.0),
            search_timeout=_env_float("SEARCH_TIMEOUT", 420.0),
            search_max_queries=max(10, _env_int("SEARCH_MAX_QUERIES", 400)),
            http_timeout=_env_float("HTTP_TIMEOUT", 15.0),
            http_workers=max(1, _env_int("HTTP_WORKERS", 8)),
            headless=_env_bool("HEADLESS", True),
        )

    def ensure_data_dir(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir
