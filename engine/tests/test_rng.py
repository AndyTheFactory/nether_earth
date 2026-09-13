import random

from nether_earth.rng import MatchRandom


def test_same_seed_produces_identical_sequences() -> None:
    first = MatchRandom(seed=1234)
    second = MatchRandom(seed=1234)

    first_sequence = [first.random() for _ in range(10)]
    second_sequence = [second.random() for _ in range(10)]

    assert first_sequence == second_sequence

    first_ints = [first.randint(0, 1000) for _ in range(10)]
    second_ints = [second.randint(0, 1000) for _ in range(10)]

    assert first_ints == second_ints

    choices = list(range(20))
    first_choices = [first.choice(choices) for _ in range(10)]
    second_choices = [second.choice(choices) for _ in range(10)]

    assert first_choices == second_choices


def test_different_seeds_are_distinguishable() -> None:
    first = MatchRandom(seed=1)
    second = MatchRandom(seed=2)

    first_sequence = [first.random() for _ in range(10)]
    second_sequence = [second.random() for _ in range(10)]

    assert first_sequence != second_sequence


def test_match_random_does_not_touch_global_random_state() -> None:
    random.seed(42)
    expected_global_sequence = [random.random() for _ in range(5)]

    random.seed(42)
    match_random = MatchRandom(seed=999)
    # Draw from the match-local RNG; this must not perturb global state.
    for _ in range(5):
        match_random.random()

    actual_global_sequence = [random.random() for _ in range(5)]

    assert actual_global_sequence == expected_global_sequence


def test_match_random_construction_does_not_read_global_random_state() -> None:
    random.seed(1)
    first = MatchRandom(seed=7)
    first_sequence = [first.random() for _ in range(5)]

    random.seed(2)
    second = MatchRandom(seed=7)
    second_sequence = [second.random() for _ in range(5)]

    assert first_sequence == second_sequence
