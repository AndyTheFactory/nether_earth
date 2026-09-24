"""Match-local seeded RNG ownership.

Per `_specs/technical-spec.md` §5.3: "If randomness is required, use a
match-local seeded RNG owned by the engine." The engine must never read or
mutate the process-global `random` module state, since that would make
concurrent matches (or any interleaving of engine calls) nondeterministic
and replay-unsafe.

``MatchRandom`` wraps a private ``random.Random(seed)`` instance so callers
always thread an explicit RNG object through engine state/services rather
than calling the top-level ``random.*`` functions.
"""

from collections.abc import MutableSequence, Sequence
from dataclasses import dataclass, field
from random import Random
from typing import TypeVar
from zlib import crc32

T = TypeVar("T")

_MASK64 = (1 << 64) - 1


def mix64(value: int) -> int:
    """Return the splitmix64 finalizer of ``value`` (64-bit, wrapping).

    Fixed integer arithmetic only: no ``hash()`` (randomized per process for
    some types), no floats, no platform-width assumptions -- so the same
    input yields the same output in every process, on every machine, in
    every replay. The primitive every ``derive_*_seed`` in the engine is
    built from (`reservations.py`'s per-tick contention seed, `navigation.py`'s
    per-robot wander seed), so there is one mixing function, not one per
    caller.
    """
    z = (value + 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return z ^ (z >> 31)


def derive_seed(*parts: int | str) -> int:
    """Return one deterministic seed mixed from ``parts``, in the order given.

    Integers are mixed directly; a string (typically an
    :class:`~nether_earth.ids.EntityId` value) is folded in through
    ``zlib.crc32`` of its UTF-8 bytes, which is stable across processes,
    platforms and Python versions in the way ``hash(str)`` is not. Different
    ``parts`` give uncorrelated streams; the same ``parts`` always give the
    same one.
    """
    seed = 0
    for part in parts:
        value = crc32(part.encode("utf-8")) if isinstance(part, str) else part
        seed = mix64((seed + value) & _MASK64)
    return seed


@dataclass(slots=True)
class MatchRandom:
    """Engine-owned seeded random source, independent of global RNG state.

    Two instances constructed with the same ``seed`` produce identical
    output sequences across any sequence of calls; this is the property
    engine determinism/replay depends on. Instances are not shared process
    globals — each match (or test) owns its own ``MatchRandom``.
    """

    seed: int
    _random: Random = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._random = Random(self.seed)

    def random(self) -> float:
        """Return the next float in ``[0.0, 1.0)`` from this match's RNG."""
        return self._random.random()

    def randint(self, a: int, b: int) -> int:
        """Return the next integer in ``[a, b]`` (inclusive) from this match's RNG."""
        return self._random.randint(a, b)

    def choice(self, sequence: Sequence[T]) -> T:
        """Return a uniformly chosen element from ``sequence``."""
        return self._random.choice(sequence)

    def shuffle(self, items: MutableSequence[T]) -> None:
        """Shuffle ``items`` in place with this match's RNG.

        The same seed always produces the same permutation of the same
        input, which is what makes a shuffled candidate order replay-safe.
        """
        self._random.shuffle(items)
