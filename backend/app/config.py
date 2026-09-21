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


@dataclass(frozen=True, slots=True)
class Settings:
    production: bool = False
    #: Public ``scheme://host[:port]`` the browser loads the app from; its
    #: origin is the only one allowed to open ``/ws`` when set.
    public_base_url: str | None = None
    replay_dir: Path | None = None

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

    return Settings(
        production=production, public_base_url=public_base_url, replay_dir=replay_dir
    )
