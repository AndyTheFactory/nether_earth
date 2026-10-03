"""War-base heli-pad landing detection.

Per `_specs/functional-spec.md` §8 ("[the commander] is used to enter
construction by landing on the player's war-base heli-pad.") and §10.3
("Construction is entered from the player's war-base heli-pad."), and
`_specs/technical-spec.md` §9 (commander model) / `docs/mechanics/commander.md` (detect a valid landing on the owning
player's heli-pad and emit the state/event needed to enter construction), this module is the detection
half of the heli-pad contract: it *detects* a valid landing and *signals*
construction-entry eligibility. It does not implement anything downstream
of that signal — no construction menus, no resource spending, no build
creation; that is `construction_session.py`'s job
(`docs/mechanics/construction.md`).

Canonical heli-pad metadata, not inferred geometry
---------------------------------------------------

The map model (`interactions.py`/`map.py`) establishes one canonical
representation for structure interaction locations — including heli-pads —
so that later systems "can be represented and queried by later engine
systems independently of physical geometry"
(`docs/mechanics/world-and-map.md`). This module therefore always
resolves a war base's heli-pad cells via
``WorldMap.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)``
and never by inspecting ``WarBase.components`` directly — the war base's
physical footprint and its heli-pad footprint are independent concepts (a
heli-pad cell need not even coincide with a physical component cell), so
one is never inferred from the other.

Ownership, not just "any war base"
-----------------------------------

A landing only counts as construction-entry eligible when the heli-pad
belongs to a war base *owned by the landing commander's own player*
(``WarBase.owner == commander.player_id``). A war base with ``owner is
None`` is neutral/unclaimed (`structures.py`); an enemy-owned war base has
a different ``owner``. Both cases must not grant construction entry — only
the commander's own war base's heli-pad does.

Landing = resting on a heli-pad cell's surface
-----------------------------------------------

"Landing" means the commander is ``FREE`` (a ``DOCKED`` commander is
already attached to a robot, not flying itself onto a pad — checked
explicitly here even though ``commander.py``'s invariants make ``DOCKED`` +
"independently landing" a contradictory combination, for clarity and so
this function's precondition list is self-documenting rather than relying
on an invariant defined in another module), with its whole 2×2 body over
one of its own war base's ``HELI_PAD`` interaction-point footprints, and at
an altitude *exactly equal* to the pad's surface height.

The heli-pad is on the war-base roof (`_specs/open-questions.md` §18):
the original game places the "H" decoration at (anchor.x,
anchor.y − 4) and enters construction only when the ship is over it at
altitude exactly 15 (`cp 15`), the roof of the 15-high war-base block. The
surface height of a pad cell is therefore the height of the static
component occupying that cell (resolved via `collision.unit_surface_height`, the
same per-cell lookup height-aware collision uses, so the landing altitude
is precisely where commander collision lets the commander settle). A pad
cell with no component is at ground level, ``rules.commander_min_altitude``.
Anything above the surface is still airborne and does not trigger
construction entry.

2×2 pad
-------

The commander is a 2×2 body anchored at its ``(x, y)`` (`occupancy.py`,
`_specs/open-questions.md` §21), and the pad is the 2×2 area of the "H"
decoration: the map declares all four pad cells, anchored at the §18 roof
location. The Spectrum's game loop only starts construction when the ship's
anchor cell *is* the "H" decoration's cell (``La6c8``:
``Lcdf5_find_building_decoration_with_ptr`` on the ship's map pointer), so
the ship's body lies exactly over the pad. Here that is "every cell of the
commander's body is a pad cell". The surface is the highest component under
the body (the war-base roof, 15), where 2×2 commander collision rests it.

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

from nether_earth.collision import unit_surface_height
from nether_earth.commander import Commander, CommanderMode
from nether_earth.events import Event, EventSequencer
from nether_earth.ids import EntityId, PlayerId
from nether_earth.interactions import InteractionKind
from nether_earth.map import WorldMap
from nether_earth.occupancy import unit_footprint_cells
from nether_earth.rules import DEFAULT_RULES, EngineRules
from nether_earth.state import GameState

__all__ = [
    "CommanderConstructionEntryEligible",
    "detect_heli_pad_landing",
    "heli_pad_surface_altitude",
]


@dataclass(frozen=True, slots=True)
class CommanderConstructionEntryEligible(Event):
    """Signals that a commander has just landed on its own war base's heli-pad.

    This is a pure *detection* event: it names the ``player`` whose
    commander landed and the ``war_base_id`` of the war base whose heli-pad
    it landed on, plus the authoritative ``tick`` the landing was detected
    on (so replay/log consumers can correlate this event with the exact
    simulation step, so replay/snapshot state is sufficient to reproduce
    the same interaction outcome without any ``snapshot.py`` support).

    Everything downstream — opening a construction menu, spending
    resources, creating a build — is out of scope here
    (`docs/mechanics/construction.md`): ``engine.step``
    reacts to this event by calling
    :func:`~nether_earth.construction_session.enter_construction`.
    """

    player: PlayerId
    war_base_id: EntityId
    tick: int


def heli_pad_surface_altitude(
    world: WorldMap, x: int, y: int, rules: EngineRules = DEFAULT_RULES
) -> int:
    """Return the altitude a commander anchored at ``(x, y)`` rests at on the pad.

    That is the highest static surface under the commander's 2×2 body
    (:func:`~nether_earth.collision.unit_surface_height`; 15 on the original
    war-base roof, `_specs/open-questions.md` §18), and never below
    ``rules.commander_min_altitude`` -- the top of the surface 2×2
    height-aware collision rests the commander on.
    """
    return max(unit_surface_height(world, x, y), rules.commander_min_altitude)


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
    - ``commander.player_id`` owns a war base in ``world.war_bases``
      (``WarBase.owner == commander.player_id`` — neither ``None``
      (neutral) nor a different player's id qualifies);
    - every cell of the commander's 2×2 body lies in one of that war base's
      ``HELI_PAD`` interaction-point footprints, resolved via
      ``world.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)``
      — never inferred from ``WarBase.components``;
    - ``commander.altitude`` equals the pad's surface height (see
      :func:`heli_pad_surface_altitude`) — the "sufficient contact"
      requirement; anything above it is still airborne.

    A war base with zero declared heli-pad interaction points simply never
    matches (``interaction_points_for`` returns an empty tuple), which is a
    valid map state, not an error.
    """
    del state  # See docstring: accepted for signature symmetry, not consulted directly.

    if commander.mode is not CommanderMode.FREE:
        return None

    for war_base in world.war_bases:
        if war_base.owner != commander.player_id:
            continue
        heli_pads = world.interaction_points_for(war_base.id, kind=InteractionKind.HELI_PAD)
        body = unit_footprint_cells(commander.x, commander.y)
        for heli_pad in heli_pads:
            if not all(cell in heli_pad.footprint.cells for cell in body):
                continue
            if commander.altitude == heli_pad_surface_altitude(
                world, commander.x, commander.y, rules
            ):
                resolved_sequencer = sequencer if sequencer is not None else EventSequencer()
                return CommanderConstructionEntryEligible(
                    sequence=resolved_sequencer.next_sequence(),
                    player=commander.player_id,
                    war_base_id=war_base.id,
                    tick=tick,
                )

    return None
