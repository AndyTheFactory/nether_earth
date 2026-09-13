"""Standalone unit tests for `nether_earth.interactions`, independent of `map.py`.

Spec references: `_specs/milestones/02-map-world-model.md`
("Structure interaction metadata"); `_specs/functional-spec.md` §8.6/§9.3/§9.4
(heli-pads/exits belong to war bases, capture applies to factories and
optionally war bases); `_specs/open-questions.md` §6 (war-base capture is not
required to be declared, so this module must not force one).
"""

import pytest

from nether_earth.ids import EntityId
from nether_earth.interactions import (
    InteractionKind,
    InteractionPoint,
    InteractionValidationError,
    parse_interaction_points,
)
from nether_earth.structures import Footprint

WAR_BASE_IDS = {"warbase-1"}
FACTORY_IDS = {"factory-1"}


def _parse(raw: list, *, war_base_ids=WAR_BASE_IDS, factory_ids=FACTORY_IDS, width=10, height=10):
    return parse_interaction_points(raw, war_base_ids, factory_ids, width=width, height=height)


# --- Every InteractionKind is representable -------------------------------------


def test_heli_pad_representable() -> None:
    points = _parse(
        [{"id": "p1", "kind": "heli_pad", "structure_id": "warbase-1", "footprint": {"x": 1, "y": 1}}]
    )
    assert points[0].kind is InteractionKind.HELI_PAD
    assert points[0].structure_id == EntityId("warbase-1")


def test_exit_representable() -> None:
    points = _parse(
        [{"id": "p1", "kind": "exit", "structure_id": "warbase-1", "footprint": {"x": 1, "y": 1}}]
    )
    assert points[0].kind is InteractionKind.EXIT


def test_factory_capture_representable() -> None:
    points = _parse(
        [
            {
                "id": "p1",
                "kind": "factory_capture",
                "structure_id": "factory-1",
                "footprint": {"x": 2, "y": 2},
            }
        ]
    )
    assert points[0].kind is InteractionKind.FACTORY_CAPTURE


def test_warbase_capture_representable() -> None:
    points = _parse(
        [
            {
                "id": "p1",
                "kind": "warbase_capture",
                "structure_id": "warbase-1",
                "footprint": {"x": 1, "y": 1},
            }
        ]
    )
    assert points[0].kind is InteractionKind.WARBASE_CAPTURE


def test_warbase_capture_absence_is_valid() -> None:
    # `_specs/open-questions.md` §6 must not be silently answered: a war base
    # with no declared WARBASE_CAPTURE point is a valid document.
    points = _parse(
        [{"id": "p1", "kind": "heli_pad", "structure_id": "warbase-1", "footprint": {"x": 1, "y": 1}}]
    )
    assert all(point.kind is not InteractionKind.WARBASE_CAPTURE for point in points)


# --- Footprint shapes ------------------------------------------------------------


def test_single_cell_footprint() -> None:
    points = _parse(
        [{"id": "p1", "kind": "exit", "structure_id": "warbase-1", "footprint": {"x": 3, "y": 4}}]
    )
    assert points[0].footprint == Footprint(cells=frozenset({(3, 4)}))


def test_multi_cell_footprint() -> None:
    points = _parse(
        [
            {
                "id": "p1",
                "kind": "exit",
                "structure_id": "warbase-1",
                "footprint": [{"x": 3, "y": 4}, {"x": 3, "y": 5}],
            }
        ]
    )
    assert points[0].footprint == Footprint(cells=frozenset({(3, 4), (3, 5)}))


# --- Structural validation --------------------------------------------------------


def test_duplicate_id_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {"id": "p1", "kind": "exit", "structure_id": "warbase-1", "footprint": {"x": 1, "y": 1}},
                {
                    "id": "p1",
                    "kind": "heli_pad",
                    "structure_id": "warbase-1",
                    "footprint": {"x": 2, "y": 1},
                },
            ]
        )


def test_unknown_kind_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "not_a_real_kind",
                    "structure_id": "warbase-1",
                    "footprint": {"x": 1, "y": 1},
                }
            ]
        )


def test_dangling_structure_id_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "exit",
                    "structure_id": "does-not-exist",
                    "footprint": {"x": 1, "y": 1},
                }
            ]
        )


def test_out_of_bounds_footprint_cell_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "exit",
                    "structure_id": "warbase-1",
                    "footprint": {"x": 100, "y": 100},
                }
            ],
            width=10,
            height=10,
        )


def test_negative_footprint_cell_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "exit",
                    "structure_id": "warbase-1",
                    "footprint": {"x": -1, "y": 0},
                }
            ]
        )


def test_out_of_bounds_cell_in_multi_cell_footprint_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "exit",
                    "structure_id": "warbase-1",
                    "footprint": [{"x": 1, "y": 1}, {"x": 50, "y": 1}],
                }
            ],
            width=10,
            height=10,
        )


# --- Kind-vs-structure-type validation --------------------------------------------


def test_heli_pad_on_factory_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "heli_pad",
                    "structure_id": "factory-1",
                    "footprint": {"x": 2, "y": 2},
                }
            ]
        )


def test_exit_on_factory_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "exit",
                    "structure_id": "factory-1",
                    "footprint": {"x": 2, "y": 2},
                }
            ]
        )


def test_warbase_capture_on_factory_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "warbase_capture",
                    "structure_id": "factory-1",
                    "footprint": {"x": 2, "y": 2},
                }
            ]
        )


def test_factory_capture_on_warbase_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(
            [
                {
                    "id": "p1",
                    "kind": "factory_capture",
                    "structure_id": "warbase-1",
                    "footprint": {"x": 1, "y": 1},
                }
            ]
        )


# --- Query API determinism ---------------------------------------------------------


def test_parse_interaction_points_is_deterministic_and_order_preserving() -> None:
    raw = [
        {"id": "p1", "kind": "heli_pad", "structure_id": "warbase-1", "footprint": {"x": 1, "y": 1}},
        {"id": "p2", "kind": "exit", "structure_id": "warbase-1", "footprint": {"x": 2, "y": 1}},
        {
            "id": "p3",
            "kind": "factory_capture",
            "structure_id": "factory-1",
            "footprint": {"x": 3, "y": 1},
        },
    ]
    first = _parse(raw)
    second = _parse(raw)
    assert first == second
    assert [point.id for point in first] == ["p1", "p2", "p3"]


def test_empty_and_none_input_yield_empty_tuple() -> None:
    assert _parse([]) == ()
    assert parse_interaction_points(None, WAR_BASE_IDS, FACTORY_IDS, width=10, height=10) == ()


def test_interaction_point_is_frozen_dataclass() -> None:
    point = InteractionPoint(
        id="p1",
        kind=InteractionKind.EXIT,
        structure_id=EntityId("warbase-1"),
        footprint=Footprint(cells=frozenset({(0, 0)})),
    )
    with pytest.raises(AttributeError):
        point.id = "other"  # type: ignore[misc]


def test_raw_not_a_list_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse({"not": "a list"})  # type: ignore[arg-type]


def test_entry_not_a_mapping_rejected() -> None:
    with pytest.raises(InteractionValidationError):
        _parse(["not-a-mapping"])  # type: ignore[list-item]
