"""Akzeptanztests: Abstand zweier Camelot-Keys auf dem Rad."""
import pytest

from sc_digger.harmonic import camelot_distance, compatible_keys


@pytest.mark.parametrize("a, b, expected", [
    ("8A", "8A", 0),
    ("8A", "9A", 1),
    ("8A", "7A", 1),
    ("12A", "1A", 1),
    ("1B", "12B", 1),
    ("8A", "8B", 1),
    ("8A", "9B", 2),
    ("8A", "2A", 6),
    ("1A", "7B", 7),
    ("3B", "10B", 5),
])
def test_distance_values(a, b, expected):
    assert camelot_distance(a, b) == expected


def test_distance_is_symmetric():
    assert camelot_distance("3A", "11B") == camelot_distance("11B", "3A") == 5


def test_input_is_normalized_like_compatible_keys():
    assert camelot_distance(" 5a ", "6A") == 1
    assert camelot_distance("12b", "12B") == 0


@pytest.mark.parametrize("bad", ["", "0A", "13A", "5C", "Am", "8", None])
def test_invalid_keys_raise_value_error(bad):
    with pytest.raises(ValueError):
        camelot_distance(bad, "8A")
    with pytest.raises(ValueError):
        camelot_distance("8A", bad)


def test_every_compatible_key_is_at_most_one_step_away():
    for n in range(1, 13):
        for letter in "AB":
            key = f"{n}{letter}"
            assert all(camelot_distance(key, k) <= 1 for k in compatible_keys(key))