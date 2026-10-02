"""Deployment settings: production must fail closed on missing/unsafe configuration (M10.2)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings, load_settings
from app.main import create_app

_PROD = {
    "NETHER_EARTH_ENV": "production",
    "NETHER_EARTH_PUBLIC_BASE_URL": "https://play.example.com",
    "NETHER_EARTH_REPLAY_DIR": "/var/lib/nether-earth/replays",
}


def test_development_defaults_need_no_environment() -> None:
    settings = load_settings({})
    assert settings.production is False
    assert settings.public_base_url is None
    assert settings.allowed_origins == frozenset()
    assert settings.replay_dir is not None


def test_production_settings_load_and_derive_origin() -> None:
    settings = load_settings(_PROD)
    assert settings.production is True
    assert settings.replay_dir == Path("/var/lib/nether-earth/replays")
    assert settings.allowed_origins == frozenset({"https://play.example.com"})


@pytest.mark.parametrize("missing", ["NETHER_EARTH_PUBLIC_BASE_URL", "NETHER_EARTH_REPLAY_DIR"])
def test_production_requires_setting(missing: str) -> None:
    env = {k: v for k, v in _PROD.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NETHER_EARTH_ENV", "staging"),
        ("NETHER_EARTH_PUBLIC_BASE_URL", "play.example.com"),
        ("NETHER_EARTH_PUBLIC_BASE_URL", "ftp://play.example.com"),
    ],
)
def test_invalid_values_fail_clearly(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_settings({**_PROD, name: value})


def test_origin_keeps_explicit_port_and_normalises_case() -> None:
    settings = load_settings({"NETHER_EARTH_PUBLIC_BASE_URL": "HTTP://LocalHost:8080/some/path"})
    assert settings.allowed_origins == frozenset({"http://localhost:8080"})


def test_production_app_hides_interactive_docs(tmp_path: Path) -> None:
    prod = TestClient(create_app(settings=Settings(production=True, replay_dir=tmp_path)))
    dev = TestClient(create_app(replay_dir=tmp_path))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert prod.get(path).status_code == 404
        assert dev.get(path).status_code == 200


def test_abandoned_lobby_grace_defaults_and_parses() -> None:
    assert load_settings({}).abandoned_lobby_grace_s == 30
    assert load_settings({"NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS": "5"}).abandoned_lobby_grace_s == 5
    with pytest.raises(ConfigError, match="NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS"):
        load_settings({"NETHER_EARTH_ABANDONED_LOBBY_GRACE_SECONDS": "0"})
