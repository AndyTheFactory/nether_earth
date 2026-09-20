"""Standard v1 world loading for the backend composition root (M7 gap fixed in M9.1).

``MatchManager`` needs a real, scenario-overlaid ``WorldMap`` for
``engine.new_game``/``MatchRuntime`` (see ``manager.py``): this module is the
one place the backend resolves *which* map file that is and how it is found
on disk. It contains no gameplay logic -- loading and overlay application are
the engine's ``map.load_world_map``/``map_overlay.default_pvp_overlay``.

Resolution order for the map directory mirrors ``app.replay.writer``'s
replay-dir convention: ``$NETHER_EARTH_MAP_DIR`` if set, else the
repository-relative ``data/maps`` (this file lives at
``backend/app/match/world.py``, so ``data/maps`` is three parents up). The
backend Docker image copies ``data/`` to ``/app/data`` and sets the env var.
"""

from __future__ import annotations

import os
from pathlib import Path

from nether_earth.map import WorldMap, load_world_map
from nether_earth.map_overlay import apply_overlay, default_pvp_overlay
from nether_earth.scenario import Scenario, default_pvp_scenario

MAP_DIR_ENV_VAR = "NETHER_EARTH_MAP_DIR"


def default_map_dir() -> Path:
    override = os.environ.get(MAP_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "maps"


def map_path_for(scenario: Scenario, map_dir: Path | None = None) -> Path:
    """Return the YAML path for ``scenario.map_id`` (file name = map id)."""
    return (map_dir if map_dir is not None else default_map_dir()) / f"{scenario.map_id}.yaml"


def load_standard_world(
    scenario: Scenario | None = None, *, map_dir: Path | None = None
) -> WorldMap:
    """Load ``scenario``'s map and apply the locked standard-PvP overlay.

    Raises ``ValueError`` if the loaded map's id/version disagree with the
    scenario, so a wrong file can never silently start a match.
    """
    resolved = scenario if scenario is not None else default_pvp_scenario()
    world = load_world_map(map_path_for(resolved, map_dir))
    if world.map_id != resolved.map_id or world.version != resolved.map_version:
        raise ValueError(
            f"map file describes {world.map_id!r} v{world.version}, scenario "
            f"{resolved.id!r} needs {resolved.map_id!r} v{resolved.map_version}"
        )
    return apply_overlay(world, default_pvp_overlay(world))
