"""Tests for construction session state and reversible build editing (issue #55, M4.5)."""

from __future__ import annotations

import copy

import pytest

from nether_earth.construction_session import (
    BuildInProgress,
    ConstructionEntryRejectionReason,
    ConstructionSession,
    DeselectModuleRejectionReason,
    SelectModuleRejectionReason,
    cancel_construction,
    deselect_module,
    enter_construction,
    select_module,
)
from nether_earth.heli_pad import CommanderConstructionEntryEligible
from nether_earth.ids import EntityId, PlayerId
from nether_earth.resource_pool import PlayerResourcePool, starting_player_resource_pool
from nether_earth.robot_build import BuildValidationError, ModuleIdentity
from nether_earth.rules import DEFAULT_RULES
from nether_earth.state import GameState, create_game_state
from nether_earth.structures import FactoryType

PLAYER_ONE = PlayerId("p1")
PLAYER_TWO = PlayerId("p2")
WAR_BASE_ONE = EntityId("warbase-p1")
WAR_BASE_TWO = EntityId("warbase-p2")


def _entry_event(
    player: PlayerId = PLAYER_ONE, war_base_id: EntityId = WAR_BASE_ONE, tick: int = 5, sequence: int = 0
) -> CommanderConstructionEntryEligible:
    return CommanderConstructionEntryEligible(
        sequence=sequence, player=player, war_base_id=war_base_id, tick=tick
    )


def _state(players: tuple[PlayerId, ...] = (PLAYER_ONE, PLAYER_TWO)) -> GameState:
    pools = tuple(starting_player_resource_pool(p, DEFAULT_RULES) for p in players)
    return create_game_state(0, players, resource_pools=pools)


# --- BuildInProgress ---------------------------------------------------------


def test_build_in_progress_defaults_empty() -> None:
    build = BuildInProgress()
    assert build.chassis is None
    assert build.weapons == ()
    assert build.electronics is None
    assert not build.is_complete()


def test_build_in_progress_rejects_too_many_weapons() -> None:
    with pytest.raises(ValueError):
        BuildInProgress(
            weapons=(
                ModuleIdentity.CANNON,
                ModuleIdentity.MISSILE,
                ModuleIdentity.PHASER,
                ModuleIdentity.NUCLEAR,
            )
        )


def test_build_in_progress_rejects_duplicate_weapon() -> None:
    with pytest.raises(ValueError):
        BuildInProgress(weapons=(ModuleIdentity.CANNON, ModuleIdentity.CANNON))


def test_build_in_progress_is_complete_requires_chassis_and_weapon() -> None:
    build = BuildInProgress(chassis=ModuleIdentity.BIPOD, weapons=(ModuleIdentity.CANNON,))
    assert build.is_complete()
    assert BuildInProgress(chassis=ModuleIdentity.BIPOD).is_complete() is False
    assert BuildInProgress(weapons=(ModuleIdentity.CANNON,)).is_complete() is False


def test_build_in_progress_to_robot_build_raises_when_incomplete() -> None:
    with pytest.raises(BuildValidationError):
        BuildInProgress(chassis=ModuleIdentity.BIPOD).to_robot_build()


def test_build_in_progress_to_robot_build_converts_when_complete() -> None:
    build = BuildInProgress(
        chassis=ModuleIdentity.TRACKS,
        weapons=(ModuleIdentity.PHASER, ModuleIdentity.CANNON),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    robot_build = build.to_robot_build()
    assert robot_build.chassis == ModuleIdentity.TRACKS
    # RobotBuild canonicalizes weapon order (cannon before phaser).
    assert robot_build.weapons == (ModuleIdentity.CANNON, ModuleIdentity.PHASER)
    assert robot_build.electronics == ModuleIdentity.ELECTRONICS


# --- enter_construction -------------------------------------------------------


def test_enter_construction_succeeds_from_valid_event() -> None:
    state = _state()
    event = _entry_event()
    result = enter_construction(state, event, PLAYER_ONE)
    assert result.accepted
    assert result.state is not None
    session = result.state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.war_base_id == WAR_BASE_ONE
    assert session.entry_tick == 5
    assert session.build == BuildInProgress()


def test_enter_construction_snapshots_actual_pool_as_buffer_and_baseline() -> None:
    pool = PlayerResourcePool(player_id=PLAYER_ONE, general=20, chassis=3, cannon=1)
    state = create_game_state(0, (PLAYER_ONE,), resource_pools=(pool,))
    result = enter_construction(state, _entry_event(), PLAYER_ONE)
    assert result.accepted
    assert result.state is not None
    session = result.state.construction_session_for(PLAYER_ONE)
    assert session is not None
    expected = pool.to_resource_pool()
    assert session.buffer == expected
    assert session.entry_snapshot == expected


def test_enter_construction_rejects_wrong_player() -> None:
    state = _state()
    event = _entry_event(player=PLAYER_ONE)
    result = enter_construction(state, event, PLAYER_TWO)
    assert not result.accepted
    assert result.reason is ConstructionEntryRejectionReason.WRONG_PLAYER
    assert result.state is None
    assert state.construction_sessions == ()


def test_enter_construction_rejects_unknown_player() -> None:
    state = create_game_state(0, (PLAYER_TWO,))
    event = _entry_event(player=PLAYER_ONE)
    result = enter_construction(state, event, PLAYER_ONE)
    assert not result.accepted
    assert result.reason is ConstructionEntryRejectionReason.UNKNOWN_PLAYER


def test_enter_construction_rejects_forged_event_without_landing() -> None:
    """A session can only be created by supplying a genuine triggering event.

    There is no other API surface to create a ConstructionSession attached
    to state -- enter_construction always requires a
    CommanderConstructionEntryEligible instance, and rejects when the event
    names a different player than the caller intends to enter for.
    """
    state = _state()
    forged_event = _entry_event(player=PLAYER_TWO, war_base_id=WAR_BASE_TWO)
    result = enter_construction(state, forged_event, PLAYER_ONE)
    assert not result.accepted
    assert result.reason is ConstructionEntryRejectionReason.WRONG_PLAYER


def test_enter_construction_rejects_when_already_in_session() -> None:
    state = _state()
    first = enter_construction(state, _entry_event(), PLAYER_ONE)
    assert first.accepted
    assert first.state is not None
    second = enter_construction(first.state, _entry_event(sequence=1), PLAYER_ONE)
    assert not second.accepted
    assert second.reason is ConstructionEntryRejectionReason.ALREADY_IN_SESSION


def test_enter_construction_does_not_touch_actual_resource_pools() -> None:
    state = _state()
    before = state.resource_pools
    result = enter_construction(state, _entry_event(), PLAYER_ONE)
    assert result.accepted
    assert result.state is not None
    assert result.state.resource_pools == before


# --- select_module -------------------------------------------------------------


def _entered(player: PlayerId = PLAYER_ONE, general: int = 20) -> GameState:
    pool = PlayerResourcePool(player_id=player, general=general)
    state = create_game_state(0, (player,), resource_pools=(pool,))
    result = enter_construction(state, _entry_event(player=player), player)
    assert result.accepted
    assert result.state is not None
    return result.state


def test_select_module_requires_active_session() -> None:
    state = _state()
    result = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD)
    assert not result.accepted
    assert result.reason is SelectModuleRejectionReason.NO_ACTIVE_SESSION


def test_select_module_updates_only_temporary_buffer() -> None:
    state = _entered()
    actual_before = state.resource_pool_for(PLAYER_ONE)
    result = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert result.accepted
    assert result.state is not None

    session = result.state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis == ModuleIdentity.BIPOD
    assert session.buffer.general == 20 - DEFAULT_RULES.module_cost_bipod

    # Actual resource pool is completely untouched.
    assert result.state.resource_pool_for(PLAYER_ONE) == actual_before


def test_select_module_rejects_duplicate_module() -> None:
    state = _entered()
    result = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert result.accepted
    assert result.state is not None
    dup = select_module(result.state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert not dup.accepted
    assert dup.reason is SelectModuleRejectionReason.DUPLICATE_MODULE
    # No partial state change.
    assert dup.state is None


def _entered_with_pool(pool: PlayerResourcePool) -> GameState:
    state = create_game_state(0, (pool.player_id,), resource_pools=(pool,))
    result = enter_construction(state, _entry_event(player=pool.player_id), pool.player_id)
    assert result.accepted
    assert result.state is not None
    return result.state


def _select_all(state: GameState, *modules: ModuleIdentity) -> GameState:
    for module in modules:
        result = select_module(state, PLAYER_ONE, module, DEFAULT_RULES)
        assert result.accepted, module
        assert result.state is not None
        state = result.state
    return state


# --- chassis swap (CR002.20, Spectrum Lca0f/Lcac1/Lca57) ---------------------


def test_select_other_chassis_swaps_with_exact_resource_accounting() -> None:
    """Bipod -> tracks: refund bipod (capped at the entry chassis amount, rest to
    general), then pay tracks chassis-pool first and general for the shortfall."""
    state = _entered_with_pool(PlayerResourcePool(player_id=PLAYER_ONE, general=10, chassis=2))
    # Bipod (3): chassis 2 -> 0, general pays the shortfall 1: 10 -> 9.
    state = _select_all(state, ModuleIdentity.BIPOD)
    session = state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert (session.buffer.amount(FactoryType.CHASSIS), session.buffer.general) == (0, 9)

    result = select_module(state, PLAYER_ONE, ModuleIdentity.TRACKS, DEFAULT_RULES)
    assert result.accepted
    assert result.removed_chassis is ModuleIdentity.BIPOD
    assert result.state is not None
    swapped = result.state.construction_session_for(PLAYER_ONE)
    assert swapped is not None
    assert swapped.build.chassis is ModuleIdentity.TRACKS
    # Refund bipod 3: chassis back to its entry amount 2, remainder 1 -> general 10.
    # Tracks 5: chassis 2 -> 0, shortfall 3 from general: 10 -> 7.
    assert (swapped.buffer.amount(FactoryType.CHASSIS), swapped.buffer.general) == (0, 7)
    assert swapped.entry_snapshot == session.entry_snapshot
    # Actual pool untouched.
    assert result.state.resource_pools == state.resource_pools


def test_chassis_swap_leaves_weapons_and_electronics_unaffected() -> None:
    state = _entered(general=100)
    state = _select_all(
        state, ModuleIdentity.CANNON, ModuleIdentity.BIPOD, ModuleIdentity.PHASER, ModuleIdentity.ELECTRONICS
    )
    result = select_module(state, PLAYER_ONE, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
    assert result.accepted
    assert result.state is not None
    session = result.state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build == BuildInProgress(
        chassis=ModuleIdentity.ANTI_GRAV,
        weapons=(ModuleIdentity.CANNON, ModuleIdentity.PHASER),
        electronics=ModuleIdentity.ELECTRONICS,
    )
    spent = (
        DEFAULT_RULES.module_cost_cannon
        + DEFAULT_RULES.module_cost_phaser
        + DEFAULT_RULES.module_cost_electronics
        + DEFAULT_RULES.module_cost_anti_grav
    )
    assert session.buffer.general == 100 - spent


def test_unaffordable_chassis_swap_is_rejected_with_old_chassis_removed_and_refunded() -> None:
    """The Spectrum refunds and removes the fitted chassis before paying for the
    new one, and its beep path (Lcaac) does not restore it: the new chassis is
    rejected and the robot is left with no chassis, the old one refunded."""
    state = _entered(general=11)
    state = _select_all(state, ModuleIdentity.BIPOD, ModuleIdentity.CANNON)
    before = state.construction_session_for(PLAYER_ONE)
    assert before is not None
    assert before.buffer.general == 11 - 3 - 2

    # After refunding bipod, 9 < anti-grav's 10.
    result = select_module(state, PLAYER_ONE, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
    assert not result.accepted
    assert result.reason is SelectModuleRejectionReason.INSUFFICIENT_RESOURCES
    assert result.removed_chassis is ModuleIdentity.BIPOD
    assert result.state is not None
    after = result.state.construction_session_for(PLAYER_ONE)
    assert after is not None
    assert after.build == BuildInProgress(weapons=(ModuleIdentity.CANNON,))
    assert after.buffer.general == 11 - 2
    assert result.state.resource_pools == state.resource_pools


def test_unaffordable_first_chassis_is_rejected_without_state_change() -> None:
    state = _entered(general=5)
    # After refunding bipod, 9 < anti-grav's 10.
    result = select_module(state, PLAYER_ONE, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
    assert not result.accepted
    assert result.reason is SelectModuleRejectionReason.INSUFFICIENT_RESOURCES
    assert result.state is None
    assert result.removed_chassis is None


def test_select_module_rejects_second_electronics() -> None:
    state = _entered()
    result = select_module(state, PLAYER_ONE, ModuleIdentity.ELECTRONICS, DEFAULT_RULES)
    assert result.accepted
    assert result.state is not None
    second = select_module(result.state, PLAYER_ONE, ModuleIdentity.ELECTRONICS, DEFAULT_RULES)
    assert not second.accepted
    # ELECTRONICS is already selected -> duplicate check fires first, which is
    # also a correct rejection (both checks agree module cannot be added again).
    assert second.reason in (
        SelectModuleRejectionReason.DUPLICATE_MODULE,
        SelectModuleRejectionReason.ELECTRONICS_ALREADY_SELECTED,
    )


def test_select_module_rejects_fourth_weapon() -> None:
    state = _entered(general=100)
    for weapon in (ModuleIdentity.CANNON, ModuleIdentity.MISSILE, ModuleIdentity.PHASER):
        result = select_module(state, PLAYER_ONE, weapon, DEFAULT_RULES)
        assert result.accepted
        assert result.state is not None
        state = result.state

    fourth = select_module(state, PLAYER_ONE, ModuleIdentity.NUCLEAR, DEFAULT_RULES)
    assert not fourth.accepted
    assert fourth.reason is SelectModuleRejectionReason.WEAPON_CAP_REACHED


def test_select_module_rejects_insufficient_resources() -> None:
    state = _entered(general=1)
    # After refunding bipod, 9 < anti-grav's 10.
    result = select_module(state, PLAYER_ONE, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
    assert not result.accepted
    assert result.reason is SelectModuleRejectionReason.INSUFFICIENT_RESOURCES
    assert result.state is None


def test_select_module_rejection_leaves_session_unchanged() -> None:
    state = _entered(general=1)
    session_before = state.construction_session_for(PLAYER_ONE)
    # After refunding bipod, 9 < anti-grav's 10.
    result = select_module(state, PLAYER_ONE, ModuleIdentity.ANTI_GRAV, DEFAULT_RULES)
    assert not result.accepted
    assert state.construction_session_for(PLAYER_ONE) == session_before


# --- deselect_module -----------------------------------------------------------


def test_deselect_module_requires_active_session() -> None:
    state = _state()
    result = deselect_module(state, PLAYER_ONE, ModuleIdentity.BIPOD)
    assert not result.accepted
    assert result.reason is DeselectModuleRejectionReason.NO_ACTIVE_SESSION


def test_deselect_module_rejects_not_selected() -> None:
    state = _entered()
    result = deselect_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert not result.accepted
    assert result.reason is DeselectModuleRejectionReason.MODULE_NOT_SELECTED


def test_deselect_module_restores_temporary_buffer_exactly() -> None:
    state = _entered(general=20)
    selected = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert selected.accepted
    assert selected.state is not None

    deselected = deselect_module(selected.state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert deselected.accepted
    assert deselected.state is not None

    session = deselected.state.construction_session_for(PLAYER_ONE)
    assert session is not None
    assert session.build.chassis is None
    # Buffer fully restored to entry-time snapshot.
    assert session.buffer == session.entry_snapshot
    assert session.buffer.general == 20


def test_deselect_module_uses_entry_time_baseline_not_drifting() -> None:
    """The refund baseline is fixed at entry, not the amount just before deselect.

    Scenario: player enters with 2 cannon-category resources already banked
    (pre-existing category stock from prior production). They select cannon
    (cost 2) twice is impossible (duplicate), so instead: select cannon,
    deselect it, select it again, then deselect -- the category pool must
    settle back to its *entry-time* value of 2 each time, not some
    intermediate drifted value.
    """
    pool = PlayerResourcePool(player_id=PLAYER_ONE, general=20, cannon=2)
    state = create_game_state(0, (PLAYER_ONE,), resource_pools=(pool,))
    entered = enter_construction(state, _entry_event(), PLAYER_ONE)
    assert entered.accepted
    assert entered.state is not None
    current = entered.state

    for _ in range(3):
        selected = select_module(current, PLAYER_ONE, ModuleIdentity.CANNON, DEFAULT_RULES)
        assert selected.accepted
        assert selected.state is not None
        deselected = deselect_module(selected.state, PLAYER_ONE, ModuleIdentity.CANNON, DEFAULT_RULES)
        assert deselected.accepted
        assert deselected.state is not None
        current = deselected.state

        session = current.construction_session_for(PLAYER_ONE)
        assert session is not None
        assert session.buffer.amount(FactoryType.CANNON) == 2
        assert session.buffer.general == 20


def test_deselect_module_never_touches_actual_resource_pool() -> None:
    state = _entered()
    actual_before = state.resource_pool_for(PLAYER_ONE)
    selected = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert selected.accepted
    assert selected.state is not None
    deselected = deselect_module(selected.state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert deselected.accepted
    assert deselected.state is not None
    assert deselected.state.resource_pool_for(PLAYER_ONE) == actual_before


# --- cancel_construction --------------------------------------------------------


def test_cancel_construction_discards_session() -> None:
    state = _entered()
    result = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert result.accepted
    assert result.state is not None
    cancelled = cancel_construction(result.state, PLAYER_ONE)
    assert cancelled.construction_session_for(PLAYER_ONE) is None


def test_cancel_construction_is_noop_without_session() -> None:
    state = _state()
    cancelled = cancel_construction(state, PLAYER_ONE)
    assert cancelled.construction_sessions == state.construction_sessions


def test_cancel_construction_leaves_actual_resources_byte_for_byte_unchanged() -> None:
    """Acceptance criterion: entry + several select/deselect + cancel changes nothing real."""
    pool = PlayerResourcePool(player_id=PLAYER_ONE, general=20)
    state = create_game_state(0, (PLAYER_ONE,), resource_pools=(pool,))
    pool_before = copy.deepcopy(state.resource_pool_for(PLAYER_ONE))

    entered = enter_construction(state, _entry_event(), PLAYER_ONE)
    assert entered.accepted
    assert entered.state is not None
    current = entered.state

    for module in (ModuleIdentity.BIPOD, ModuleIdentity.CANNON, ModuleIdentity.ELECTRONICS):
        selected = select_module(current, PLAYER_ONE, module, DEFAULT_RULES)
        assert selected.accepted
        assert selected.state is not None
        current = selected.state

    deselected = deselect_module(current, PLAYER_ONE, ModuleIdentity.CANNON, DEFAULT_RULES)
    assert deselected.accepted
    assert deselected.state is not None
    current = deselected.state

    reselected = select_module(current, PLAYER_ONE, ModuleIdentity.MISSILE, DEFAULT_RULES)
    assert reselected.accepted
    assert reselected.state is not None
    current = reselected.state

    final = cancel_construction(current, PLAYER_ONE)

    assert final.resource_pool_for(PLAYER_ONE) == pool_before
    assert final.construction_session_for(PLAYER_ONE) is None


# --- GameState attachment / replay-safety --------------------------------------


def test_construction_sessions_must_be_a_participant() -> None:
    state = create_game_state(0, (PLAYER_TWO,))
    session = ConstructionSession(
        player_id=PLAYER_ONE,
        war_base_id=WAR_BASE_ONE,
        entry_tick=0,
        build=BuildInProgress(),
        buffer=PlayerResourcePool(player_id=PLAYER_ONE).to_resource_pool(),
        entry_snapshot=PlayerResourcePool(player_id=PLAYER_ONE).to_resource_pool(),
    )
    with pytest.raises(ValueError):
        state.with_construction_sessions((session,))


def test_construction_sessions_reject_duplicate_player() -> None:
    session_a = ConstructionSession(
        player_id=PLAYER_ONE,
        war_base_id=WAR_BASE_ONE,
        entry_tick=0,
        build=BuildInProgress(),
        buffer=PlayerResourcePool(player_id=PLAYER_ONE).to_resource_pool(),
        entry_snapshot=PlayerResourcePool(player_id=PLAYER_ONE).to_resource_pool(),
    )
    session_b = ConstructionSession(
        player_id=PLAYER_ONE,
        war_base_id=WAR_BASE_TWO,
        entry_tick=1,
        build=BuildInProgress(),
        buffer=PlayerResourcePool(player_id=PLAYER_ONE).to_resource_pool(),
        entry_snapshot=PlayerResourcePool(player_id=PLAYER_ONE).to_resource_pool(),
    )
    state = create_game_state(0, (PLAYER_ONE,))
    with pytest.raises(ValueError):
        state.with_construction_sessions((session_a, session_b))


def test_construction_sessions_canonical_order() -> None:
    state = _state((PLAYER_ONE, PLAYER_TWO))
    entered_one = enter_construction(state, _entry_event(player=PLAYER_ONE), PLAYER_ONE)
    assert entered_one.accepted
    assert entered_one.state is not None
    entered_two = enter_construction(
        entered_one.state,
        _entry_event(player=PLAYER_TWO, war_base_id=WAR_BASE_TWO, sequence=1),
        PLAYER_TWO,
    )
    assert entered_two.accepted
    assert entered_two.state is not None

    player_ids = tuple(s.player_id for s in entered_two.state.construction_sessions)
    assert player_ids == tuple(sorted(player_ids, key=lambda p: p.value))


def test_construction_session_structural_round_trip_equality() -> None:
    state = _entered()
    result = select_module(state, PLAYER_ONE, ModuleIdentity.BIPOD, DEFAULT_RULES)
    assert result.accepted
    assert result.state is not None
    session = result.state.construction_session_for(PLAYER_ONE)
    assert session is not None

    rebuilt = ConstructionSession(
        player_id=session.player_id,
        war_base_id=session.war_base_id,
        entry_tick=session.entry_tick,
        build=session.build,
        buffer=session.buffer,
        entry_snapshot=session.entry_snapshot,
    )
    assert rebuilt == session
    assert hash(rebuilt.build) == hash(session.build)
