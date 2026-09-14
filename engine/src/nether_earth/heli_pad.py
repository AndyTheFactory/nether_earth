"""War-base heli-pad landing detection (issue #41, M3.5).

Per `_specs/functional-spec.md` §8 ("[the commander] is used to enter
construction by landing on the player's war-base heli-pad.") and §10.3
("Construction is entered from the player's war-base heli-pad."), and
`_specs/technical-spec.md` §9 (commander model) / `_specs/milestones/
03-commander-movement-docking.md` ("War-base heli-pad interaction" —
"Detect valid landing on the owning player's heli-pad and emit the
state/event needed to enter construction"), this module is the M3 half of
the heli-pad contract: it *detects* a valid landing and *signals*
construction-entry eligibility. It does not implement anything downstream
of that signal — no construction menus, no resource spending, no build
creation. That is M4's scope
(`_specs/milestones/04-robots-construction-economy.md`), which explicitly
lists "Milestone 3 heli-pad/commander interaction contract" as its input.

Canonical heli-pad metadata, not inferred geometry
---------------------------------------------------

M2 (`interactions.py`/`map.py`) already establishes one canonical
representation for structure interaction locations — including heli-pads —
so that later systems "can be represented and queried by later engine
systems independently of physical geometry"
(`_specs/milestones/02-map-world-model.md`). This module therefore always
resolves a war base's heli-pad cells via
``WorldMap.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)``
and never by inspecting ``WarBase.components`` directly — the war base's
physical footprint and its heli-pad footprint are independent concepts (a
heli-pad cell need not even coincide with a physical component cell), and
issue #41 is explicit that inferring one from the other is out of scope.

Ownership, not just "any war base"
-----------------------------------

A landing only counts as construction-entry eligible when the heli-pad
belongs to a war base *owned by the landing commander's own player*
(``WarBase.owner == commander.player_id``). A war base with ``owner is
None`` is neutral/unclaimed (`structures.py`); an enemy-owned war base has
a different ``owner``. Both cases must not grant construction entry — only
the commander's own war base's heli-pad does.

Landing = grounded + on a heli-pad cell
-----------------------------------------

"Landing" means the commander is ``FREE`` (a ``DOCKED`` commander is
already attached to a robot, not flying itself onto a pad — checked
explicitly here even though #37's invariants make ``DOCKED`` +
"independently landing" a contradictory combination, for clarity and so
this function's precondition list is self-documenting rather than relying
on an invariant defined in another module), at
``rules.commander_min_altitude`` (grounded — the "sufficient contact"
acceptance criterion; anything above ground level is still airborne and
must not trigger construction entry even if directly above a heli-pad
cell), and at an ``(x, y)`` that is a member of one of its own war base's
``HELI_PAD`` interaction-point footprints.

Determinism and "exactly one event"
--------------------------------------

:func:`detect_heli_pad_landing` is a pure function of its arguments (plus
the caller-supplied ``sequencer``, whose state is itself deterministic —
see ``events.py``), so the same ``(state, world, commander, tick)`` always
produces the same result: no wall-clock reads, no unordered-collection
iteration that could affect the outcome. It returns at most one event.
Practically, a valid map's heli-pad footprints never overlap between
different war bases (each cell belongs to at most one structure's
interaction geometry), so a commander position can match at most one
war base's heli-pad. Defensively, if a map ever did declare overlapping
heli-pad footprints across the *landing player's own* war bases, this
function still picks deterministically: it iterates ``world.war_bases`` in
the map's declared order (a stable tuple, per ``map.py``'s determinism
note) and, for the first matching war base, ``interaction_points_for``
also preserves declared order — so the result never depends on set/dict
iteration order.
"""

from __future__ import annotations

from dataclasses import dataclass

from nether_earth.commander import Commander, CommanderMode
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "CommanderConstructionEntryEligible",
    "detect_heli_pad_landing",
]


@dataclass(frozen=True, slots=True)
class CommanderConstructionEntryEligible(Event):
    """Signals that a commander has just landed on its own war base's heli-pad.

    This is a pure *detection* event: it names the ``player`` whose
    commander landed and the ``war_base_id`` of the war base whose heli-pad
    it landed on, plus the authoritative ``tick`` the landing was detected
    on (so replay/log consumers can correlate this event with the exact
    simulation step, matching the "replay/snapshot state is sufficient to
    reproduce the same interaction outcome" acceptance criterion without
    requiring any change to ``snapshot.py`` — see issue #41's scope notes).

    Everything downstream — opening a construction menu, spending
    resources, creating a build — is explicitly out of scope here and owned
    by M4 (`_specs/milestones/04-robots-construction-economy.md`). Nothing
    in this module or ``engine.py`` currently consumes this event; it exists
    so a future integration point (and M4) has a concrete, documented
    signal to react to.
    """

    player: PlayerId
    war_base_id: EntityId
    tick: int


def detect_heli_pad_landing(
    state: GameState,
    world: WorldMap,
    commander: Commander,
    tick: int,
    rules: EngineRules = DEFAULT_RULES,
    sequencer: EventSequencer | None = None,
) -> CommanderConstructionEntryEligible | None:
    """Return a construction-entry event if ``commander`` has just landed on its own heli-pad.

    ``state`` is accepted (and ``commander`` is expected to be — but is not
    required to be — ``state.commander_for(commander.player_id)``) purely
    so this function's signature matches the rest of the engine's
    ``(state, world, ..., tick)`` convention and so a future caller that
    only has ``state``/``world`` on hand can pass ``state.commander_for(...)``
    without an extra lookup; the detection logic itself only reads fields
    off ``commander`` and ``world`` and is otherwise independent of
    ``state``. This keeps the function safely reusable from a per-commander
    loop keyed by ``state.commanders`` (canonical order, see ``state.py``)
    without also reaching back into ``state`` for anything else.

    ``sequencer`` follows the same convention as ``engine.py``'s
    ``CommandAccepted``/``CommandRejected`` emission: callers that need
    engine-wide deterministic ordering across multiple events in one tick
    supply a shared :class:`~nether_earth.events.EventSequencer`. When
    omitted, a fresh sequencer (starting at 0) is used, since a single
    detection call in isolation has nothing else to order against.

    Returns ``None`` unless *all* of the following hold:

    - ``commander.mode is CommanderMode.FREE`` — a docked commander is not
      independently landing (see module docstring);
    - ``commander.altitude == rules.commander_min_altitude`` — grounded,
      the "sufficient contact" requirement (anything else, including a
      commander merely close to the ground, does not count);
    - ``commander.player_id`` owns a war base in ``world.war_bases``
      (``WarBase.owner == commander.player_id`` — neither ``None``
      (neutral) nor a different player's id qualifies);
    - ``(commander.x, commander.y)`` is a member of that war base's
      ``HELI_PAD`` interaction-point footprint(s), resolved via
      ``world.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)``
      — never inferred from ``WarBase.components``.

    A war base with zero declared heli-pad interaction points simply never
    matches (``interaction_points_for`` returns an empty tuple), which is a
    valid map state, not an error.
    """
    del state  # See docstring: accepted for signature symmetry, not consulted directly.

    if commander.mode is not CommanderMode.FREE:
        return None
    if commander.altitude != rules.commander_min_altitude:
        return None

    for war_base in world.war_bases:
        if war_base.owner != commander.player_id:
            continue
        heli_pads = world.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)
        for heli_pad in heli_pads:
            if (commander.x, commander.y) in heli_pad.footprint.cells:
                resolved_sequencer = sequencer if sequencer is not None else EventSequencer()
                return CommanderConstructionEntryEligible(
                    sequence=resolved_sequencer.next_sequence(),
                    player=commander.player_id,
                    war_base_id=war_base.id,
                    tick=tick,
                )

    return None
