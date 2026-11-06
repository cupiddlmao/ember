"""Runtime configuration for the Ember mobile API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """A configuration problem that should stop startup with a clear message."""


def _positive_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigurationError(f"{name} must be a whole number.") from None
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}.")
    return value


@dataclass(frozen=True)
class Settings:
    token: str
    host: str = "127.0.0.1"
    port: int = 8080
    library_dir: Path = Path("/var/lib/ember/library")
    cache_dir: Path = Path("/var/lib/ember/cache")
    cache_max_bytes: int = 2 * 1024 ** 3
    max_search_limit: int = 25
    max_audio_bytes: int = 48 * 1024 ** 2
    max_concurrent_jobs: int = 3
    rate_limit_per_minute: int = 60

    def __post_init__(self):
        if self.library_dir.resolve().is_relative_to(self.cache_dir.resolve()):
            raise ConfigurationError("EMBER_LIBRARY_DIR must be outside the evictable audio cache.")

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("EMBER_API_TOKEN", "").strip()
        if len(token) < 24 or token.lower().startswith(("replace-", "changeme", "change-me")):
            raise ConfigurationError(
                "Set EMBER_API_TOKEN to a random value of at least 24 characters "
                "(for example: openssl rand -hex 32)."
            )
        return cls(
            token=token,
            host=os.environ.get("EMBER_BIND", "127.0.0.1").strip() or "127.0.0.1",
            port=_positive_int("EMBER_PORT", 8080),
            library_dir=Path(os.environ.get("EMBER_LIBRARY_DIR", "/var/lib/ember/library")),
            cache_dir=Path(os.environ.get("EMBER_CACHE_DIR", "/var/lib/ember/cache")),
            cache_max_bytes=_positive_int("EMBER_CACHE_MAX_MB", 2048) * 1024 * 1024,
            max_audio_bytes=_positive_int("EMBER_MAX_AUDIO_MB", 48) * 1024 * 1024,
            max_concurrent_jobs=_positive_int("EMBER_MAX_JOBS", 3),
            rate_limit_per_minute=_positive_int("EMBER_RATE_LIMIT", 60),
        )
