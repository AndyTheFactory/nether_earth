"""Tests for ``app.match.manager.MatchManager`` (issue #92).

Covers the acceptance criteria from the M7 Task 2 brief: multiple matches
coexist with no shared state, exactly two player slots are enforced,
ready/start happens exactly once and initializes one authoritative engine
state, session tokens resolve correctly and cannot cross matches/slots, and
lookup/removal behaves correctly.
"""

from __future__ import annotations

import pytest
from nether_earth import engine as engine_module
from nether_earth.ids import PLAYER_ONE, PLAYER_TWO

from app.match import (
    InvalidNicknameError,
    InvalidSessionTokenError,
    MatchFullError,
    MatchManager,
    MatchNotFoundError,
    MatchRuntimeState,
)


@pytest.fixture
def manager() -> MatchManager:
    return MatchManager()


# -- create -------------------------------------------------------------------


def test_create_match_seats_first_player_as_player_one(manager: MatchManager) -> None:
    result = manager.create_match("alice")

    assert result.player_id == PLAYER_ONE
    match = manager.get_match(result.match_id)
    assert match.state is MatchRuntimeState.WAITING
    assert match.join_code == result.join_code
    assert set(match.players) == {PLAYER_ONE}
    assert match.players[PLAYER_ONE].nickname == "alice"
    assert match.players[PLAYER_ONE].session_token == result.session_token
    assert match.game_state is None


def test_create_match_rejects_blank_nickname(manager: MatchManager) -> None:
    with pytest.raises(InvalidNicknameError):
        manager.create_match("   ")


def test_join_codes_and_session_tokens_are_unique_across_matches(manager: MatchManager) -> None:
    first = manager.create_match("alice")
    second = manager.create_match("bob")

    assert first.match_id != second.match_id
    assert first.join_code != second.join_code
    assert first.session_token != second.session_token


# -- join ----------------------------------------------------------------------


def test_join_seats_second_player_as_player_two(manager: MatchManager) -> None:
    created = manager.create_match("alice")

    joined = manager.join_match(created.join_code, "bob")

    assert joined.match_id == created.match_id
    assert joined.player_id == PLAYER_TWO
    assert joined.session_token != created.session_token
    match = manager.get_match(created.match_id)
    assert match.is_full
    assert match.players[PLAYER_TWO].nickname == "bob"


def test_join_unknown_code_raises(manager: MatchManager) -> None:
    with pytest.raises(MatchNotFoundError):
        manager.join_match("NOSUCH", "bob")


def test_join_rejects_over_capacity(manager: MatchManager) -> None:
    created = manager.create_match("alice")
    manager.join_match(created.join_code, "bob")

    with pytest.raises(MatchFullError):
        manager.join_match(created.join_code, "carol")

    # The rejected join must not have mutated the existing two-slot match.
    match = manager.get_match(created.match_id)
    assert set(match.players) == {PLAYER_ONE, PLAYER_TWO}


def test_join_rejects_blank_nickname(manager: MatchManager) -> None:
    created = manager.create_match("alice")
    with pytest.raises(InvalidNicknameError):
        manager.join_match(created.join_code, "")


def test_join_rejects_nickname_equal_to_creator(manager: MatchManager) -> None:
    created = manager.create_match("alice")
    for clash in ("alice", "Alice", "ａｌｉｃｅ"):
        with pytest.raises(InvalidNicknameError):
            manager.join_match(created.join_code, clash)
    assert not manager.get_match(created.match_id).is_full

    joined = manager.join_match(created.join_code, "alice2")
    assert joined.player_id == PLAYER_TWO


# -- readiness / start -----------------------------------------------------------


def test_ready_transition_requires_both_players(manager: MatchManager) -> None:
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")

    manager.set_ready(created.session_token)
    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.WAITING
    assert match.game_state is None

    manager.set_ready(joined.session_token)
    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.ACTIVE
    assert match.game_state is not None
    assert match.game_state.tick == 0
    assert set(match.game_state.players) == {PLAYER_ONE, PLAYER_TWO}
    assert match.game_state.seed == match.seed


def test_start_calls_engine_new_game_exactly_once(
    manager: MatchManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original_new_game = engine_module.new_game

    def counting_new_game(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return original_new_game(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_module, "new_game", counting_new_game)

    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")

    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)
    assert len(calls) == 1

    # Toggling readiness after the match is already ACTIVE must not re-run
    # the start transition (no un-start/re-start in v1).
    manager.set_ready(created.session_token, ready=False)
    manager.set_ready(created.session_token, ready=True)
    assert len(calls) == 1


def test_ready_with_only_one_player_present_never_starts(manager: MatchManager) -> None:
    created = manager.create_match("alice")

    manager.set_ready(created.session_token)

    match = manager.get_match(created.match_id)
    assert match.state is MatchRuntimeState.WAITING
    assert match.game_state is None


# -- session token resolution -----------------------------------------------------


def test_session_token_resolves_to_correct_player_and_match(manager: MatchManager) -> None:
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")

    match_a, player_a = manager.resolve_session(created.session_token)
    match_b, player_b = manager.resolve_session(joined.session_token)

    assert match_a.match_id == created.match_id
    assert player_a == PLAYER_ONE
    assert match_b.match_id == created.match_id
    assert player_b == PLAYER_TWO


def test_session_token_cannot_cross_matches(manager: MatchManager) -> None:
    first = manager.create_match("alice")
    second = manager.create_match("carol")

    match, player = manager.resolve_session(first.session_token)
    assert match.match_id == first.match_id
    assert player == PLAYER_ONE

    match, player = manager.resolve_session(second.session_token)
    assert match.match_id == second.match_id
    assert player == PLAYER_ONE  # both are PLAYER_ONE in their own match, not interchangeable
    assert match.match_id != first.match_id


def test_unknown_session_token_raises(manager: MatchManager) -> None:
    with pytest.raises(InvalidSessionTokenError):
        manager.resolve_session("not-a-real-token")


# -- lookup / removal --------------------------------------------------------------


def test_multiple_matches_coexist_without_state_leakage(manager: MatchManager) -> None:
    a = manager.create_match("alice")
    b = manager.create_match("carol")
    manager.join_match(a.join_code, "bob")
    manager.join_match(b.join_code, "dave")

    manager.set_ready(a.session_token)
    match_a = manager.get_match(a.match_id)
    match_b = manager.get_match(b.match_id)

    # Readying only a's PLAYER_ONE must not affect b at all.
    assert match_a.players[PLAYER_ONE].ready is True
    assert match_b.players[PLAYER_ONE].ready is False
    assert match_a.state is MatchRuntimeState.WAITING
    assert match_b.state is MatchRuntimeState.WAITING
    assert len(manager) == 2


def test_get_match_unknown_id_raises(manager: MatchManager) -> None:
    with pytest.raises(MatchNotFoundError):
        manager.get_match("nonexistent")


def test_get_match_by_join_code_unknown_raises(manager: MatchManager) -> None:
    with pytest.raises(MatchNotFoundError):
        manager.get_match_by_join_code("NOSUCH")


def test_finish_match_sets_finished_state(manager: MatchManager) -> None:
    created = manager.create_match("alice")

    match = manager.finish_match(created.match_id)

    assert match.state is MatchRuntimeState.FINISHED
    # Idempotent.
    match = manager.finish_match(created.match_id)
    assert match.state is MatchRuntimeState.FINISHED


def test_finish_match_unknown_id_raises(manager: MatchManager) -> None:
    with pytest.raises(MatchNotFoundError):
        manager.finish_match("nonexistent")


def test_dispose_match_removes_all_indices(manager: MatchManager) -> None:
    created = manager.create_match("alice")
    joined = manager.join_match(created.join_code, "bob")
    manager.finish_match(created.match_id)

    manager.dispose_match(created.match_id)

    assert len(manager) == 0
    with pytest.raises(MatchNotFoundError):
        manager.get_match(created.match_id)
    with pytest.raises(MatchNotFoundError):
        manager.get_match_by_join_code(created.join_code)
    with pytest.raises(InvalidSessionTokenError):
        manager.resolve_session(created.session_token)
    with pytest.raises(InvalidSessionTokenError):
        manager.resolve_session(joined.session_token)


def test_dispose_match_unknown_id_raises(manager: MatchManager) -> None:
    with pytest.raises(MatchNotFoundError):
        manager.dispose_match("nonexistent")


def test_disposing_one_match_does_not_affect_another(manager: MatchManager) -> None:
    a = manager.create_match("alice")
    b = manager.create_match("carol")

    manager.dispose_match(a.match_id)

    assert len(manager) == 1
    match_b = manager.get_match(b.match_id)
    assert match_b.match_id == b.match_id


# -- deterministic seed plumbing --------------------------------------------------


def test_explicit_seed_is_recorded_on_started_game_state(manager: MatchManager) -> None:
    created = manager.create_match("alice", seed=12345)
    joined = manager.join_match(created.join_code, "bob")

    manager.set_ready(created.session_token)
    manager.set_ready(joined.session_token)

    match = manager.get_match(created.match_id)
    assert match.seed == 12345
    assert match.game_state is not None
    assert match.game_state.seed == 12345


@pytest.mark.parametrize(
    "nickname",
    [
        "\u200b\u200b",  # zero-width spaces only
        "ali\u200bce",  # zero-width space inside
        "bob\u2060",  # word joiner
        "\ufeffcarol",  # BOM
        "\u3000\u3000",  # ideographic spaces only (stripped to empty)
        "\u0301\u0301",  # combining marks only, nothing visible
        "al\u200dice",  # ZWJ between letters (not allowed)
        "\u200d\U0001f600",  # ZWJ before emoji (not allowed, nothing before ZWJ)
        "\u3164",  # Hangul filler: a "letter" that renders blank
        "\u2800\u2800",  # braille blank pattern
        "al\u034fice",  # combining grapheme joiner
    ],
)
def test_nickname_invisible_or_format_characters_rejected(manager: MatchManager, nickname: str) -> None:
    with pytest.raises(InvalidNicknameError):
        manager.create_match(nickname)


@pytest.mark.parametrize("nickname", ["alice", "Zoë", "山田", "player-1", "😀", "\U0001f469\u200d\U0001f680 pilot"])
def test_nickname_visible_unicode_accepted(manager: MatchManager, nickname: str) -> None:
    result = manager.create_match(nickname)
    assert manager.get_match(result.match_id).players[PLAYER_ONE].nickname == nickname

