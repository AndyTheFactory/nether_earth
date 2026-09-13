from nether_earth.ids import PLAYER_ONE, PLAYER_TWO, EntityId, PlayerId


def test_player_id_is_hashable_and_equality_comparable() -> None:
    a = PlayerId("p1")
    b = PlayerId("p1")
    c = PlayerId("p2")

    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert {a, b, c} == {PlayerId("p1"), PlayerId("p2")}


def test_player_id_round_trips_through_json_safe_primitive() -> None:
    original = PlayerId("p1")
    primitive = original.to_json()

    assert primitive == "p1"
    assert isinstance(primitive, str)
    assert PlayerId.from_json(primitive) == original


def test_entity_id_is_hashable_and_round_trips() -> None:
    original = EntityId("robot-0001")
    primitive = original.to_json()

    assert isinstance(primitive, str)
    assert EntityId.from_json(primitive) == original
    assert hash(EntityId(primitive)) == hash(original)


def test_canonical_v1_player_constants_are_distinct_and_stable() -> None:
    assert PLAYER_ONE != PLAYER_TWO
    assert PLAYER_ONE == PlayerId("p1")
    assert PLAYER_TWO == PlayerId("p2")


def test_player_id_and_entity_id_are_ordered_for_deterministic_sorting() -> None:
    ids = [PlayerId("p2"), PlayerId("p1")]
    assert sorted(ids) == [PlayerId("p1"), PlayerId("p2")]
