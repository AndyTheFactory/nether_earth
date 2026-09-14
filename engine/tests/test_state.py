from nether_earth.commander import Commander, CommanderMode
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, PlayerId
from nether_earth.state import GameState, create_game_state


def test_game_state_constructs_independently_with_authoritative_tick() -> None:
    state = GameState(tick=0, players=(PLAYER_ONE, PLAYER_TWO))

    assert state.tick == 0
    assert state.players == (PLAYER_ONE, PLAYER_TWO)


def test_game_state_is_frozen() -> None:
    state = GameState(tick=0, players=())

    try:
        state.tick = 5  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("GameState must be immutable")


def test_with_tick_returns_new_state_without_mutating_original() -> None:
    state = GameState(tick=0, players=(PLAYER_ONE,))
    advanced = state.with_tick(5)

    assert state.tick == 0
    assert advanced.tick == 5
    assert advanced.players == state.players
    assert advanced is not state


def test_create_game_state_orders_players_canonically_regardless_of_input_order() -> None:
    from_one_order = create_game_state(0, [PLAYER_TWO, PLAYER_ONE])
    from_other_order = create_game_state(0, [PLAYER_ONE, PLAYER_TWO])

    assert from_one_order.players == (PLAYER_ONE, PLAYER_TWO)
    assert from_one_order == from_other_order


def test_create_game_state_deduplicates_players() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_ONE, PLAYER_TWO])
    assert state.players == (PLAYER_ONE, PLAYER_TWO)


def test_create_game_state_accepts_set_input_and_is_still_deterministic() -> None:
    first = create_game_state(0, {PlayerId("p9"), PlayerId("p2"), PlayerId("p1")})
    second = create_game_state(0, [PlayerId("p1"), PlayerId("p9"), PlayerId("p2")])

    assert first.players == (PlayerId("p1"), PlayerId("p2"), PlayerId("p9"))
    assert first == second


def test_create_game_state_rejects_negative_tick() -> None:
    try:
        create_game_state(-1, [])
    except ValueError:
        pass
    else:
        raise AssertionError("negative tick must be rejected")


def test_game_state_defaults_to_no_commanders() -> None:
    state = GameState(tick=0, players=(PLAYER_ONE, PLAYER_TWO))

    assert state.commanders == ()
    assert state.commander_for(PLAYER_ONE) is None


def test_create_game_state_attaches_and_orders_commanders_canonically() -> None:
    commander_two = Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=1, y=1, altitude=0)
    commander_one = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=2, y=2, altitude=0)

    state = create_game_state(
        0, [PLAYER_ONE, PLAYER_TWO], commanders=[commander_two, commander_one]
    )

    assert state.commanders == (commander_one, commander_two)
    assert state.commander_for(PLAYER_ONE) == commander_one
    assert state.commander_for(PLAYER_TWO) == commander_two


def test_create_game_state_rejects_commander_for_unknown_player() -> None:
    orphan = Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=0, y=0, altitude=0)

    try:
        create_game_state(0, [PLAYER_ONE], commanders=[orphan])
    except ValueError:
        pass
    else:
        raise AssertionError("commander owned by a non-participant player must be rejected")


def test_create_game_state_rejects_duplicate_commander_owner() -> None:
    first = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)
    second = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=1, y=1, altitude=0)

    try:
        create_game_state(0, [PLAYER_ONE], commanders=[first, second])
    except ValueError:
        pass
    else:
        raise AssertionError("two commanders sharing a player_id must be rejected")


def test_with_commanders_returns_new_state_without_mutating_original() -> None:
    state = create_game_state(0, [PLAYER_ONE, PLAYER_TWO])
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)

    updated = state.with_commanders((commander,))

    assert state.commanders == ()
    assert updated.commanders == (commander,)
    assert updated is not state
    assert updated.tick == state.tick
    assert updated.players == state.players
    assert updated.seed == state.seed


def test_with_commanders_rejects_commander_for_unknown_player() -> None:
    state = create_game_state(0, [PLAYER_ONE])
    orphan = Commander(player_id=PLAYER_TWO, mode=CommanderMode.FREE, x=0, y=0, altitude=0)

    try:
        state.with_commanders((orphan,))
    except ValueError:
        pass
    else:
        raise AssertionError("commander owned by a non-participant player must be rejected")


def test_with_tick_carries_commanders_over_unchanged() -> None:
    commander = Commander(player_id=PLAYER_ONE, mode=CommanderMode.FREE, x=0, y=0, altitude=0)
    state = create_game_state(0, [PLAYER_ONE], commanders=[commander])

    advanced = state.with_tick(1)

    assert advanced.commanders == (commander,)
