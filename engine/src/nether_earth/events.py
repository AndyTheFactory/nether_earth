"""Base engine event contract and deterministic event ordering.

This module defines the *shape* that all future gameplay events (production,
capture, combat, docking, victory, ...) will extend. No concrete gameplay
event exists yet; those are introduced by later milestones on top of the
:class:`Event` base.

Ordering convention: events produced within a single tick must have a
deterministic order independent of the order Python happened to collect them
in (e.g. iterating a ``dict``/``set`` of affected entities). Every event
carries an explicit ``sequence`` assigned at emission time via
:class:`EventSequencer`, not inferred from list-append order of an otherwise
unordered source. :func:`order_events` then sorts any collection of events
back into canonical order.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    """Base contract for all engine-level events.

    Concrete gameplay events (not defined in this milestone) subclass
    ``Event`` and add their own fields. Every event must carry ``sequence``:
    a non-negative integer assigned by the emitting code, in emission order,
    via :class:`EventSequencer` (or an equivalent explicit counter) - never
    left implicit in the order a Python collection happens to yield events.
    """

    sequence: int


def event_sort_key(event: Event) -> tuple[int, str]:
    """Return the deterministic ordering key for ``event``.

    Events sort by their assigned ``sequence`` first. ``repr(event)`` is a
    secondary tie-break so that, in the degenerate case of two events
    sharing a ``sequence`` (a caller bug, since sequences should be unique
    per tick), the resulting order is still fully deterministic and
    independent of collection/insertion order rather than falling back to
    Python's input-order-dependent stable sort.
    """
    return (event.sequence, repr(event))


def order_events(events: Iterable[Event]) -> tuple[Event, ...]:
    """Return ``events`` sorted into canonical deterministic order.

    The same logical set of events (regardless of the iteration order of
    the collection it is supplied in - list, set, generator, shuffled list,
    ...) always sorts to an identical result.
    """
    return tuple(sorted(events, key=event_sort_key))


class EventSequencer:
    """Assigns deterministic, monotonically increasing event sequence numbers.

    Centralizes the per-tick sequence counter so call sites emitting events
    (in a deterministic emission order of their own choosing - e.g. iterating
    entities in canonical id order) do not each invent an ad hoc counter.
    Not thread-safe; the engine is single-threaded per tick by design.
    """

    __slots__ = ("_next",)

    def __init__(self, start: int = 0) -> None:
        self._next = start

    def next_sequence(self) -> int:
        """Return the next sequence number and advance the counter."""
        value = self._next
        self._next += 1
        return value
