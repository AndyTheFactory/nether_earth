"""Deterministic ground-level solid occupancy index.

Per `_specs/technical-spec.md` §8 and `_specs/functional-spec.md` §7.3, a
grid cell may have at most one ground-level solid occupant. This module is
the one authoritative occupancy API later systems (movement, construction,
capture) must use rather than re-deriving occupancy from raw structure/robot
lists.

This module builds and maintains a cell -> entity index and enforces the
one-solid-per-cell invariant deterministically (issue #23):

- ``from_structures`` walks war bases/factories/blockers in ``id.value``
  order so a conflict (if any) and the resulting grid never depend on
  incidental input ordering.
- ``with_added``/``with_removed`` are the dynamic-update API for later
  systems (robots, construction, capture). ``with_added`` enforces that an
  entity occupies exactly one footprint at a time: it rejects both a
  footprint that overlaps another entity's cells and an attempt to add a
  second, possibly-disjoint footprint for an entity that already occupies
  cells in this grid — callers must ``with_removed`` an entity before
  re-adding it elsewhere.
- ``cells()`` returns occupied cells in sorted order so results are
  byte-for-byte reproducible across independently-built, canonically-equal
  grids, not just set-equal.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nether_earth.ids import EntityId
from nether_earth.structures import occupied_cells

if TYPE_CHECKING:
    from nether_earth.structures import Blocker, Factory, Footprint, WarBase


class OccupancyConflictError(ValueError):
    """Raised when two entities' footprints would occupy the same ground cell."""


@dataclass(frozen=True, slots=True)
class OccupancyGrid:
    """An immutable ground-solid occupancy index: cell -> occupying entity id.

    Construct via :meth:`from_structures` (initial map load) or derive a new
    instance via :meth:`with_added`/:meth:`with_removed` (dynamic updates).
    Never mutate ``_occupants`` directly.
    """

    _occupants: "dict[tuple[int, int], EntityId]" = field(default_factory=dict)

    @classmethod
    def from_structures(
        cls,
        war_bases: "Iterable[WarBase]",
        factories: "Iterable[Factory]",
        blockers: "Iterable[Blocker]",
    ) -> "OccupancyGrid":
        """Build an occupancy grid from a map's war bases, factories, and blockers.

        Structures are walked in a stable, deterministic order (sorted by
        ``id.value``) regardless of the order they were parsed/supplied in,
        so the resulting conflict error (if any) and grid contents never
        depend on incidental list ordering. Raises :class:`OccupancyConflictError`
        naming both conflicting entity ids if two structures' occupied cells
        share a cell.
        """
        all_structures: list[WarBase | Factory | Blocker] = [
            *war_bases,
            *factories,
            *blockers,
        ]
        occupants: dict[tuple[int, int], EntityId] = {}
        for structure in sorted(all_structures, key=lambda structure: structure.id.value):
            for cell in sorted(occupied_cells(structure)):
                existing = occupants.get(cell)
                if existing is not None:
                    raise OccupancyConflictError(
                        f"cell {cell} is claimed by both {existing.value!r} and "
                        f"{structure.id.value!r}"
                    )
                occupants[cell] = structure.id
        return cls(_occupants=occupants)

    def is_occupied(self, x: int, y: int) -> bool:
        """Return whether ``(x, y)`` has a ground-solid occupant."""
        return (x, y) in self._occupants

    def occupant_at(self, x: int, y: int) -> EntityId | None:
        """Return the occupying entity id at ``(x, y)``, or ``None`` if empty."""
        return self._occupants.get((x, y))

    def cells(self) -> tuple[tuple[int, int], ...]:
        """Return the currently occupied cells in a stable, sorted order.

        Sorted (rather than raw dict/set iteration) so that two grids built
        independently from canonically-equal inputs always produce identical
        output here, regardless of insertion order.
        """
        return tuple(sorted(self._occupants.keys()))

    def with_added(self, entity_id: EntityId, footprint: "Footprint") -> "OccupancyGrid":
        """Return a new grid with ``entity_id`` occupying ``footprint``.

        Raises :class:`OccupancyConflictError` if any cell in ``footprint``
        is already occupied by another entity, or if ``entity_id`` already
        occupies any cell in this grid. Ground-level solid occupancy is
        one footprint per entity at a time: a caller that wants to move
        ``entity_id`` to a new footprint must :meth:`with_removed` it first
        rather than accumulating a second, disjoint footprint under the same
        id.
        """
        if any(occupant == entity_id for occupant in self._occupants.values()):
            raise OccupancyConflictError(
                f"{entity_id.value!r} already occupies cells in this grid; "
                "remove it before adding a new footprint"
            )
        new_occupants = dict(self._occupants)
        for cell in sorted(footprint.cells):
            existing = new_occupants.get(cell)
            if existing is not None:
                raise OccupancyConflictError(
                    f"cell {cell} is already claimed by {existing.value!r}; "
                    f"cannot add {entity_id.value!r}"
                )
            new_occupants[cell] = entity_id
        return OccupancyGrid(_occupants=new_occupants)

    def with_removed(self, entity_id: EntityId) -> "OccupancyGrid":
        """Return a new grid with all cells occupied by ``entity_id`` cleared.

        Raises ``KeyError`` if ``entity_id`` does not currently occupy any
        cell in this grid.
        """
        remaining = {
            cell: occupant for cell, occupant in self._occupants.items() if occupant != entity_id
        }
        if len(remaining) == len(self._occupants):
            raise KeyError(f"{entity_id.value!r} does not occupy any cell in this grid")
        return OccupancyGrid(_occupants=remaining)
