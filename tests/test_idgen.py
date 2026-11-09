from app.idgen import ALPHABET, CODE_LENGTH, SPACE, generate_code, scramble


def test_codes_are_fixed_length_base62():
    for n in (1, 2, 12345, SPACE - 1):
        code = generate_code(n, "k")
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(ALPHABET)


def test_no_collisions_over_many_ids():
    codes = {generate_code(n, "secret") for n in range(1, 200_000)}
    assert len(codes) == 199_999  # bijection => all unique


def test_consecutive_ids_look_unrelated():
    a, b = generate_code(1, "secret"), generate_code(2, "secret")
    assert a != b
    assert sum(x == y for x, y in zip(a, b)) <= 3  # not a shared prefix


def test_secret_changes_output_and_is_deterministic():
    assert generate_code(7, "one") == generate_code(7, "one")
    assert generate_code(7, "one") != generate_code(7, "two")


def test_scramble_stays_in_range():
    for n in (0, 1, SPACE - 1):
        assert 0 <= scramble(n, "k") < SPACE
