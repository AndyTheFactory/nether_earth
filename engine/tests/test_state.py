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
