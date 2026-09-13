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

from collections.abc import Sequence
from dataclasses import dataclass, field
from random import Random
from typing import TypeVar

T = TypeVar("T")


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
