import random
from dataclasses import dataclass

from nether_earth.events import Event, EventSequencer, event_sort_key, order_events


@dataclass(frozen=True, slots=True)
class _NoopEvent(Event):
    """Minimal concrete event used only to exercise the base contract.

    No gameplay events exist yet (later milestones define those); this
    test-local subclass just proves the base ``Event``/ordering contract
    works for any concrete event shape.
    """

    label: str = ""


def test_order_events_is_deterministic_regardless_of_collection_order() -> None:
    events = [
        _NoopEvent(sequence=4, label="d"),
        _NoopEvent(sequence=0, label="a"),
        _NoopEvent(sequence=2, label="c"),
        _NoopEvent(sequence=1, label="b"),
    ]

    shuffled = list(events)
    random.Random(99).shuffle(shuffled)

    expected = order_events(events)
    assert order_events(shuffled) == expected
    assert order_events(reversed(events)) == expected
    assert [event.sequence for event in expected] == [0, 1, 2, 4]


def test_order_events_from_unordered_source_is_still_deterministic() -> None:
    """Simulate events collected from an unordered source (e.g. a set).

    Even though ``set`` iteration order is not guaranteed, the events
    themselves carry an explicit ``sequence`` assigned at emission time, so
    sorting them back always recovers the same canonical order.
    """
    events = {
        _NoopEvent(sequence=10, label="x"),
        _NoopEvent(sequence=3, label="y"),
        _NoopEvent(sequence=7, label="z"),
    }

    assert order_events(events) == order_events(list(events)[::-1])
    assert [event.sequence for event in order_events(events)] == [3, 7, 10]


def test_event_sequencer_assigns_monotonic_sequence_in_emission_order() -> None:
    sequencer = EventSequencer()

    first = sequencer.next_sequence()
    second = sequencer.next_sequence()
    third = sequencer.next_sequence()

    assert (first, second, third) == (0, 1, 2)


def test_event_sequencer_start_offset_is_respected() -> None:
    sequencer = EventSequencer(start=5)

    assert sequencer.next_sequence() == 5
    assert sequencer.next_sequence() == 6


def test_order_events_tie_breaks_deterministically_on_duplicate_sequence() -> None:
    """Duplicate sequence numbers are a caller bug, but ordering must still
    be fully deterministic (not dependent on input order) even then."""
    a = _NoopEvent(sequence=1, label="a")
    b = _NoopEvent(sequence=1, label="b")

    forward = order_events([a, b])
    backward = order_events([b, a])

    assert forward == backward
    assert forward == tuple(sorted([a, b], key=event_sort_key))
