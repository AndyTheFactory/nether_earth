"""Stable, serializable identifier types for players and entities.

Conventions:

- Ids are frozen, slotted dataclasses wrapping a single JSON-safe primitive
  (``str``). This keeps them hashable and equality-comparable by value, and
  trivially serializable for canonical snapshots without
  embedding any non-serializable object on the id itself.
- v1 scope is a 2-player PvP match. ``PlayerId`` is not hardcoded to the
  literal strings "p1"/"p2" scattered around calling code; the canonical
  values for the two v1 players are centralized here as ``PLAYER_ONE`` and
  ``PLAYER_TWO``.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class PlayerId:
    """Stable identifier for a match participant."""

    value: str

    def to_json(self) -> str:
        """Return the JSON-safe primitive representation of this id."""
        return self.value

    @classmethod
    def from_json(cls, value: str) -> "PlayerId":
        """Reconstruct a ``PlayerId`` from its JSON-safe primitive."""
        return cls(value)


#: Canonical v1 PvP player identifiers. Calling code should reference these
#: constants rather than scattering the literal strings "p1"/"p2".
PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")


@dataclass(frozen=True, slots=True, order=True)
class EntityId:
    """Stable identifier for a game entity (robot, factory, war base, ...)."""

    value: str

    def to_json(self) -> str:
        """Return the JSON-safe primitive representation of this id."""
        return self.value

    @classmethod
    def from_json(cls, value: str) -> "EntityId":
        """Reconstruct an ``EntityId`` from its JSON-safe primitive."""
        return cls(value)
