from nether_earth import engine_identity


def test_engine_package_is_deterministic_and_importable() -> None:
    assert engine_identity() == "nether-earth-engine"
    assert engine_identity() == engine_identity()
