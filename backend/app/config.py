"""Deployment-only runtime settings, read from ``NETHER_EARTH_*`` environment variables.

Gameplay rules are never configured here (technical spec §24): these are
process/transport/operations knobs only. ``NETHER_EARTH_ENV=production``
(set by the backend Docker image) turns every missing or unsafe value into a
startup failure instead of a silent development default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.replay.writer import default_replay_dir


class ConfigError(ValueError):
    """Raised when the environment does not describe a safe, usable configuration."""


DEFAULT_MAX_MATCHES = 200
DEFAULT_FINISHED_RETENTION_S = 300
DEFAULT_WAITING_TIMEOUT_S = 900
DEFAULT_ABANDONED_LOBBY_GRACE_S = 30
DEFAULT_REPLAY_RETENTION_DAYS = 5
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_LOG_FORMATS = ("json", "text")


@dataclass(frozen=True, slots=True)
class Settings:
    production: bool = False
    #: Public ``scheme://host[:port]`` the browser loads the app from; its
    #: origin is the only one allowed to open ``/ws`` when set.
    public_base_url: str | None = None
    replay_dir: Path | None = None
    #: Upper bound on matches held in memory at once (any state).
    max_matches: int = DEFAULT_MAX_MATCHES
    #: Seconds a finished match stays resolvable (reconnecting players still
    #: get the result) before it is dropped from memory.
    finished_retention_s: int = DEFAULT_FINISHED_RETENTION_S
    #: Seconds a lobby may wait for its second player before it is dropped.
    waiting_timeout_s: int = DEFAULT_WAITING_TIMEOUT_S
    #: Seconds a lobby survives with no socket attached (page refresh grace)
    #: before its capacity is released.
    abandoned_lobby_grace_s: int = DEFAULT_ABANDONED_LOBBY_GRACE_S
    #: Days a finished/interrupted replay artifact is kept before the
    #: backend deletes it; ``None`` keeps everything. Unset env: 5 days in
    #: production, ``None`` in development; ``0``/``forever`` forces ``None``
    #: (see `_specs/resolved-questions.md`, Replay retention default).
    replay_retention_days: int | None = None
    log_level: str = "INFO"
    #: ``json`` (one object per line; production default) or ``text``.
    log_format: str = "text"

    @property
    def allowed_origins(self) -> frozenset[str]:
        if self.public_base_url is None:
            return frozenset()
        return frozenset({_origin_of(self.public_base_url)})


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"NETHER_EARTH_PUBLIC_BASE_URL must be an http(s) URL, got {url!r}")
    return f"{parts.scheme}://{parts.netloc}".lower()


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name) or None
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be > 0, got {raw!r}")
    return value


def _retention_days(env: Mapping[str, str], name: str, default: int | None) -> int | None:
    """Positive int -> days; ``0``/``forever`` -> ``None`` (keep all); unset -> ``default``."""
    raw = env.get(name) or None
    if raw is None:
        return default
    if raw.strip().lower() in ("0", "forever"):
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a positive integer, 0 or 'forever', got {raw!r}") from None
    if value < 0:
        raise ConfigError(f"{name} must be a positive integer, 0 or 'forever', got {raw!r}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build :class:`Settings` from ``env`` (default ``os.environ``), validating every value."""
    env = os.environ if env is None else env
    environment = env.get("NETHER_EARTH_ENV", "development")
    if environment not in ("production", "development"):
        raise ConfigError(f"NETHER_EARTH_ENV must be production or development, got {environment!r}")
    production = environment == "production"

    public_base_url = env.get("NETHER_EARTH_PUBLIC_BASE_URL") or None
    if public_base_url is not None:
        _origin_of(public_base_url)  # validate early
    elif production:
        raise ConfigError("NETHER_EARTH_PUBLIC_BASE_URL is required when NETHER_EARTH_ENV=production")

    replay_raw = env.get("NETHER_EARTH_REPLAY_DIR") or None
    if replay_raw is None and production:
        raise ConfigError("NETHER_EARTH_REPLAY_DIR is required when NETHER_EARTH_ENV=production")
    replay_dir = Path(replay_raw) if replay_raw else default_replay_dir()

    log_level = env.get("NETHER_EARTH_LOG_LEVEL", "INFO").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"NETHER_EARTH_LOG_LEVEL must be one of {', '.join(_LOG_LEVELS)}")
    log_format = env.get("NETHER_EARTH_LOG_FORMAT", "json" if production else "text").lower()
    if log_format not in _LOG_FORMATS:
        raise ConfigError(f"NETHER_EARTH_LOG_FORMAT must be one of {', '.join(_LOG_FORMATS)}")

    return Settings(
        production=production,
        public_base_url=public_base_url,
        replay_dir=replay_dir,
        max_matches=_positive_int(env, "NETHER_EARTH_MAX_MATCHES", DEFAULT_MAX_MATCHES),
        finished_retention_s=_positive_int(
            env, "NETHER_EARTH_FINISHED_MATCH_RETENTION_SECONDS", DEFAULT_FINISHED_RETENTION_S
        ),
        waiting_timeout_s=_positive_int(
            env, "NETHER_EARTH_WAITING_MATCH_TIMEOUT_SECONDS", DEFAULT_WAITING_TIMEOUT_S
        ),
        abandoned_lobby_grace_s=_positive_int(
            env, "NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS", DEFAULT_ABANDONED_LOBBY_GRACE_S
        ),
        replay_retention_days=_retention_days(
            env,
            "NETHER_EARTH_REPLAY_RETENTION_DAYS",
            DEFAULT_REPLAY_RETENTION_DAYS if production else None,
        ),
        log_level=log_level,
        log_format=log_format,
    )
