from pathlib import Path

from nether_earth.map import BootstrapMap, load_bootstrap_map


def test_bootstrap_map_loads_from_repository_fixture() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    loaded = load_bootstrap_map(repo_root / "data" / "maps" / "bootstrap.yaml")

    assert loaded == BootstrapMap(
        map_id="bootstrap",
        version=1,
        width=1,
        height=1,
    )
