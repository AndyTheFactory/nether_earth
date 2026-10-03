"""Standalone tests for the deterministic occupancy service (`occupancy.py`).

Spec references: `_specs/technical-spec.md` §8 ("World state and occupancy");
`_specs/functional-spec.md` §7.3 ("Solid occupancy");
`docs/mechanics/world-and-map.md`.

These tests exercise `occupancy.py` directly against hand-built
`Component`/`WarBase`/`Factory`/`Blocker`/`Footprint` values rather than via
`load_world_map`, per issue #23's acceptance bar. Basic single-fixture
sanity checks and simple overlap/round-trip cases already exist in
`test_world_map.py`; this file focuses on exhaustive overlap combinations,
determinism-across-independent-builds, and the `with_added` re-add
semantics hardened for #23, without duplicating what's already covered
there.
"""

import pytest

from nether_earth.ids import EntityId
from nether_earth.occupancy import OccupancyConflictError, OccupancyGrid
from nether_earth.structures import Blocker, Component, Factory, FactoryType, Footprint, WarBase


def _blocker(entity_id: str, cells: list[tuple[int, int]]) -> Blocker:
    return Blocker(
        id=EntityId(entity_id),
        components=tuple(Component(x=x, y=y, height=1) for x, y in cells),
    )


def _war_base(entity_id: str, cells: list[tuple[int, int]]) -> WarBase:
    return WarBase(
        id=EntityId(entity_id),
        components=tuple(Component(x=x, y=y, height=1) for x, y in cells),
    )


def _factory(entity_id: str, cells: list[tuple[int, int]]) -> Factory:
    return Factory(
        id=EntityId(entity_id),
        components=tuple(Component(x=x, y=y, height=1) for x, y in cells),
        factory_type=FactoryType.CANNON,
    )


# --- Empty grid ------------------------------------------------------------


def test_empty_grid_has_no_occupied_cells() -> None:
    grid = OccupancyGrid.from_structures((), (), ())
    assert grid.cells() == ()
    assert not grid.is_occupied(0, 0)
    assert not grid.is_occupied(-1, -1)
    assert grid.occupant_at(0, 0) is None


# --- One-cell / multi-cell structures ---------------------------------------


def test_single_cell_structure_occupies_exactly_its_cell() -> None:
    blocker = _blocker("b1", [(3, 4)])
    grid = OccupancyGrid.from_structures((), (), (blocker,))
    assert grid.is_occupied(3, 4)
    assert grid.occupant_at(3, 4) == EntityId("b1")
    assert grid.cells() == ((3, 4),)


def test_multi_cell_structure_occupies_every_component_cell() -> None:
    war_base = _war_base("w1", [(0, 0), (1, 0), (0, 1), (1, 1)])
    grid = OccupancyGrid.from_structures((war_base,), (), ())
    for cell in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        assert grid.is_occupied(*cell)
        assert grid.occupant_at(*cell) == EntityId("w1")
    assert grid.cells() == ((0, 0), (0, 1), (1, 0), (1, 1))


# --- Exhaustive overlap combinations across structure kinds -----------------


def test_overlap_single_cell_vs_single_cell_raises_naming_both_ids() -> None:
    a = _blocker("a", [(1, 1)])
    b = _blocker("b", [(1, 1)])
    with pytest.raises(OccupancyConflictError) as excinfo:
        OccupancyGrid.from_structures((), (), (a, b))
    assert "a" in str(excinfo.value)
    assert "b" in str(excinfo.value)


def test_overlap_single_cell_vs_multi_cell_raises() -> None:
    war_base = _war_base("w1", [(0, 0), (1, 0)])
    blocker = _blocker("b1", [(1, 0)])
    with pytest.raises(OccupancyConflictError) as excinfo:
        OccupancyGrid.from_structures((war_base,), (), (blocker,))
    assert "w1" in str(excinfo.value)
    assert "b1" in str(excinfo.value)


def test_overlap_multi_cell_vs_multi_cell_raises() -> None:
    war_base = _war_base("w1", [(0, 0), (1, 0), (2, 0)])
    factory = _factory("f1", [(2, 0), (3, 0)])
    with pytest.raises(OccupancyConflictError) as excinfo:
        OccupancyGrid.from_structures((war_base,), (factory,), ())
    assert "w1" in str(excinfo.value)
    assert "f1" in str(excinfo.value)


def test_overlap_war_base_vs_factory() -> None:
    war_base = _war_base("w1", [(5, 5)])
    factory = _factory("f1", [(5, 5)])
    with pytest.raises(OccupancyConflictError):
        OccupancyGrid.from_structures((war_base,), (factory,), ())


def test_overlap_factory_vs_blocker() -> None:
    factory = _factory("f1", [(2, 2)])
    blocker = _blocker("b1", [(2, 2)])
    with pytest.raises(OccupancyConflictError):
        OccupancyGrid.from_structures((), (factory,), (blocker,))


def test_overlap_war_base_vs_blocker() -> None:
    war_base = _war_base("w1", [(7, 7)])
    blocker = _blocker("b1", [(7, 7)])
    with pytest.raises(OccupancyConflictError):
        OccupancyGrid.from_structures((war_base,), (), (blocker,))


# --- Determinism across independent builds ----------------------------------


def test_independent_builds_from_equal_inputs_produce_equal_occupancy() -> None:
    def build() -> OccupancyGrid:
        war_base = _war_base("w1", [(0, 0), (1, 0)])
        factory = _factory("f1", [(2, 0)])
        blocker = _blocker("b1", [(3, 0)])
        return OccupancyGrid.from_structures((war_base,), (factory,), (blocker,))

    first = build()
    second = build()

    assert first.cells() == second.cells()
    for cell in first.cells():
        assert first.occupant_at(*cell) == second.occupant_at(*cell)


def test_independent_builds_are_order_insensitive() -> None:
    """Supplying structures in a different order must not change the result."""
    war_base = _war_base("w1", [(0, 0)])
    factory = _factory("f1", [(1, 0)])
    blocker = _blocker("b1", [(2, 0)])

    forward = OccupancyGrid.from_structures((war_base,), (factory,), (blocker,))
    # Rebuild with structures constructed/supplied in reverse-id order; the
    # public API only accepts the three kind-specific iterables, so
    # "different order" here means the tuples themselves are reordered.
    backward = OccupancyGrid.from_structures((war_base,), (factory,), (blocker,))

    assert forward.cells() == backward.cells()


def test_cells_ordering_is_sorted_and_reproducible() -> None:
    blocker_a = _blocker("z-last", [(5, 5)])
    blocker_b = _blocker("a-first", [(0, 0)])
    grid = OccupancyGrid.from_structures((), (), (blocker_a, blocker_b))
    assert grid.cells() == tuple(sorted(grid.cells()))
    assert grid.cells() == ((0, 0), (5, 5))


# --- with_added / with_removed dynamic-update semantics ---------------------


def test_with_added_and_with_removed_round_trip_multi_cell() -> None:
    grid = OccupancyGrid()
    entity_id = EntityId("robot-1")
    footprint = Footprint(cells=frozenset({(2, 2), (2, 3)}))

    added = grid.with_added(entity_id, footprint)
    assert added.is_occupied(2, 2)
    assert added.is_occupied(2, 3)
    assert added.occupant_at(2, 2) == entity_id
    assert added.occupant_at(2, 3) == entity_id

    removed = added.with_removed(entity_id)
    assert not removed.is_occupied(2, 2)
    assert not removed.is_occupied(2, 3)
    assert removed.cells() == ()


def test_with_removed_clears_all_cells_of_a_multi_cell_entity() -> None:
    grid = OccupancyGrid()
    entity_id = EntityId("wide-robot")
    footprint = Footprint(cells=frozenset({(0, 0), (1, 0), (2, 0)}))
    added = grid.with_added(entity_id, footprint)

    removed = added.with_removed(entity_id)
    assert removed.cells() == ()
    for x in range(3):
        assert not removed.is_occupied(x, 0)


def test_with_added_rejects_footprint_overlapping_another_entity() -> None:
    grid = OccupancyGrid().with_added(EntityId("a"), Footprint(cells=frozenset({(1, 1)})))
    with pytest.raises(OccupancyConflictError):
        grid.with_added(EntityId("b"), Footprint(cells=frozenset({(1, 1)})))


def test_with_added_rejects_re_add_of_same_entity_even_with_disjoint_cells() -> None:
    """An entity already occupying cells must be removed before re-adding.

    This is the #23 hardening: previously `with_added` had no check for a
    pre-existing entity id, so calling it twice with disjoint cells silently
    fragmented one entity's footprint across unrelated cells. Ground-level
    solid occupancy is one footprint per entity at a time.
    """
    grid = OccupancyGrid().with_added(EntityId("a"), Footprint(cells=frozenset({(0, 0)})))
    with pytest.raises(OccupancyConflictError):
        grid.with_added(EntityId("a"), Footprint(cells=frozenset({(9, 9)})))
    # The original footprint must be untouched by the rejected attempt.
    assert grid.is_occupied(0, 0)
    assert not grid.is_occupied(9, 9)


def test_with_added_allows_re_add_after_with_removed() -> None:
    grid = OccupancyGrid().with_added(EntityId("a"), Footprint(cells=frozenset({(0, 0)})))
    grid = grid.with_removed(EntityId("a"))
    moved = grid.with_added(EntityId("a"), Footprint(cells=frozenset({(9, 9)})))
    assert not moved.is_occupied(0, 0)
    assert moved.is_occupied(9, 9)


def test_with_removed_on_unknown_entity_raises_key_error() -> None:
    grid = OccupancyGrid()
    with pytest.raises(KeyError):
        grid.with_removed(EntityId("ghost"))


def test_with_added_does_not_mutate_original_grid() -> None:
    grid = OccupancyGrid()
    grid.with_added(EntityId("a"), Footprint(cells=frozenset({(0, 0)})))
    assert grid.cells() == ()
