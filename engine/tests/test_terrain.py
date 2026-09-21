"""Focused unit tests for the terrain model, isolated from `map.load_world_map`.

Spec references: `_specs/technical-spec.md` §7.3 ("Terrain movement");
`_specs/functional-spec.md` §7.2 ("Terrain");
`_specs/milestones/02-map-world-model.md` (Terrain model workstream / acceptance
criteria).

This module intentionally does not exercise `map.py`/YAML-file loading (that
is covered by `test_world_map.py`); it drives `terrain.py`'s public surface
directly: `TerrainType`, `TerrainGrid`, `parse_terrain_grid`, and
`TerrainValidationError`.
"""

import pytest

from nether_earth.terrain import (
    TerrainGrid,
    TerrainType,
    TerrainValidationError,
    parse_terrain_grid,
)

# --- TerrainType representability / round-trip ----------------------------------


@pytest.mark.parametrize("terrain_type", list(TerrainType))
def test_every_terrain_type_round_trips_through_parse(terrain_type: TerrainType) -> None:
    grid = parse_terrain_grid(
        {"cells": [{"x": 0, "y": 0, "type": terrain_type.value}]}, width=2, height=2
    )
    assert grid.terrain_at(0, 0) is terrain_type


def test_terrain_type_has_required_members() -> None:
    names = {member.name for member in TerrainType}
    assert names == {"NORMAL", "ROUGH", "MOUNTAIN", "DITCH"}
    assert TerrainType("mountain") is TerrainType.MOUNTAIN


# --- Determinism / equality ------------------------------------------------------


def test_terrain_at_is_deterministic_across_repeated_calls() -> None:
    grid = parse_terrain_grid({"cells": [{"x": 1, "y": 1, "type": "ditch"}]}, width=3, height=3)
    results = [grid.terrain_at(1, 1) for _ in range(50)]
    assert all(result is TerrainType.DITCH for result in results)

    default_results = [grid.terrain_at(0, 0) for _ in range(50)]
    assert all(result is TerrainType.NORMAL for result in default_results)


def test_equal_inputs_produce_equal_independent_grids() -> None:
    raw = {"default": "normal", "cells": [{"x": 2, "y": 0, "type": "rough"}]}
    first = parse_terrain_grid(raw, width=4, height=4)
    second = parse_terrain_grid(raw, width=4, height=4)
    assert first is not second
    assert first == second
    assert first.terrain_at(2, 0) == second.terrain_at(2, 0)


def test_equal_inputs_produce_equal_grids_regardless_of_cell_order() -> None:
    first = parse_terrain_grid(
        {"cells": [{"x": 0, "y": 0, "type": "rough"}, {"x": 1, "y": 0, "type": "ditch"}]},
        width=3,
        height=3,
    )
    second = parse_terrain_grid(
        {"cells": [{"x": 1, "y": 0, "type": "ditch"}, {"x": 0, "y": 0, "type": "rough"}]},
        width=3,
        height=3,
    )
    assert first == second


def test_directly_constructed_grid_is_not_mutable_via_caller_dict() -> None:
    cells = {(0, 0): TerrainType.ROUGH}
    grid = TerrainGrid(width=2, height=2, cells=cells)
    cells[(0, 0)] = TerrainType.DITCH
    cells[(1, 1)] = TerrainType.DITCH
    # Mutating the dict the caller passed in must not retroactively change
    # an already-constructed grid's query results.
    assert grid.terrain_at(0, 0) is TerrainType.ROUGH
    assert grid.terrain_at(1, 1) is TerrainType.NORMAL


# --- Bounds behavior --------------------------------------------------------------


def test_terrain_at_rejects_negative_x() -> None:
    grid = parse_terrain_grid(None, width=3, height=3)
    with pytest.raises(ValueError):
        grid.terrain_at(-1, 0)


def test_terrain_at_rejects_negative_y() -> None:
    grid = parse_terrain_grid(None, width=3, height=3)
    with pytest.raises(ValueError):
        grid.terrain_at(0, -1)


def test_terrain_at_rejects_x_at_or_beyond_width() -> None:
    grid = parse_terrain_grid(None, width=3, height=3)
    with pytest.raises(ValueError):
        grid.terrain_at(3, 0)


def test_terrain_at_rejects_y_at_or_beyond_height() -> None:
    grid = parse_terrain_grid(None, width=3, height=3)
    with pytest.raises(ValueError):
        grid.terrain_at(0, 3)


def test_terrain_at_accepts_all_four_corners_in_bounds() -> None:
    grid = parse_terrain_grid(None, width=3, height=3)
    assert grid.terrain_at(0, 0) is TerrainType.NORMAL
    assert grid.terrain_at(2, 0) is TerrainType.NORMAL
    assert grid.terrain_at(0, 2) is TerrainType.NORMAL
    assert grid.terrain_at(2, 2) is TerrainType.NORMAL


# --- Validation: malformed cell entries -------------------------------------------


def test_parse_terrain_grid_rejects_cell_missing_x() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"y": 0, "type": "rough"}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_cell_missing_y() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": 0, "type": "rough"}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_cell_missing_type() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": 0, "y": 0}]}, width=2, height=2)


@pytest.mark.parametrize("bad_x", ["0", 1.5, None, [0]])
def test_parse_terrain_grid_rejects_non_integer_x(bad_x: object) -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": bad_x, "y": 0, "type": "rough"}]}, width=2, height=2)


@pytest.mark.parametrize("bad_y", ["0", 1.5, None, [0]])
def test_parse_terrain_grid_rejects_non_integer_y(bad_y: object) -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": 0, "y": bad_y, "type": "rough"}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_bool_as_coordinate() -> None:
    # bool is a subclass of int in Python; must not be silently accepted as
    # a coordinate.
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": True, "y": 0, "type": "rough"}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_unknown_type_string() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": 0, "y": 0, "type": "lava"}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_non_string_type() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": 0, "y": 0, "type": 1}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_duplicate_cell_coordinates() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid(
            {"cells": [{"x": 0, "y": 0, "type": "rough"}, {"x": 0, "y": 0, "type": "ditch"}]},
            width=2,
            height=2,
        )


def test_parse_terrain_grid_rejects_out_of_bounds_cell() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": [{"x": 5, "y": 0, "type": "rough"}]}, width=2, height=2)


def test_parse_terrain_grid_rejects_cells_not_a_list() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": {"x": 0, "y": 0, "type": "rough"}}, width=2, height=2)


def test_parse_terrain_grid_rejects_cell_entry_not_a_mapping() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"cells": ["not-a-mapping"]}, width=2, height=2)


def test_parse_terrain_grid_rejects_non_mapping_section() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid("not-a-mapping", width=2, height=2)  # type: ignore[arg-type]


def test_parse_terrain_grid_rejects_unknown_default_type() -> None:
    with pytest.raises(TerrainValidationError):
        parse_terrain_grid({"default": "lava"}, width=2, height=2)


def test_parse_terrain_grid_validation_is_deterministic() -> None:
    raw = {"cells": [{"x": 0, "y": 0, "type": "rough"}, {"x": 1, "y": 0, "type": "lava"}]}
    first_message = None
    for _ in range(5):
        with pytest.raises(TerrainValidationError) as excinfo:
            parse_terrain_grid(raw, width=3, height=3)
        if first_message is None:
            first_message = str(excinfo.value)
        else:
            assert str(excinfo.value) == first_message


# --- Empty/absent section and custom default --------------------------------------


def test_absent_terrain_section_yields_all_default_grid() -> None:
    grid = parse_terrain_grid(None, width=4, height=4)
    for x in range(4):
        for y in range(4):
            assert grid.terrain_at(x, y) is TerrainType.NORMAL


def test_empty_terrain_section_yields_all_default_grid() -> None:
    grid = parse_terrain_grid({}, width=4, height=4)
    for x in range(4):
        for y in range(4):
            assert grid.terrain_at(x, y) is TerrainType.NORMAL


def test_custom_non_normal_default_applies_to_unlisted_cells() -> None:
    grid = parse_terrain_grid(
        {"default": "rough", "cells": [{"x": 1, "y": 1, "type": "ditch"}]}, width=3, height=3
    )
    assert grid.terrain_at(0, 0) is TerrainType.ROUGH
    assert grid.terrain_at(2, 2) is TerrainType.ROUGH
    assert grid.terrain_at(1, 1) is TerrainType.DITCH


def test_custom_default_via_direct_construction() -> None:
    grid = TerrainGrid(width=2, height=2, cells={}, default=TerrainType.DITCH)
    assert grid.terrain_at(0, 0) is TerrainType.DITCH
    assert grid.terrain_at(1, 1) is TerrainType.DITCH


# --- TerrainGrid direct construction guards ----------------------------------------


def test_terrain_grid_rejects_non_positive_width() -> None:
    with pytest.raises(ValueError):
        TerrainGrid(width=0, height=2, cells={})


def test_terrain_grid_rejects_non_positive_height() -> None:
    with pytest.raises(ValueError):
        TerrainGrid(width=2, height=0, cells={})
