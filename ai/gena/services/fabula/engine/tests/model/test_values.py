from datetime import timedelta

import pytest

from ai.gena.services.fabula.engine.model.durations import DurationError, format_duration, parse_duration
from ai.gena.services.fabula.engine.model.jsonvalue import exceeds_size, json_equal, merge_patch


@pytest.mark.parametrize(
    "text, expected",
    [
        ("PT1S", timedelta(seconds=1)),
        ("P3D", timedelta(days=3)),
        ("P1DT2H30M", timedelta(days=1, hours=2, minutes=30)),
        ("PT0.5S", timedelta(milliseconds=500)),
        ("P2W", timedelta(days=14)),
        ({"days": 1, "milliseconds": 5}, timedelta(days=1, milliseconds=5)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["P1Y", "P1M", "PT", "P", "1 day", {"weeks": 1}, {}, {"days": -1}, 5])
def test_parse_duration_rejects(text):
    with pytest.raises(DurationError):
        parse_duration(text)


def test_format_duration_round_trips():
    for value in (timedelta(0), timedelta(days=3), timedelta(hours=1, milliseconds=250), timedelta(minutes=90)):
        assert parse_duration(format_duration(value)) == value


def test_json_equal_distinguishes_booleans_from_numbers():
    assert json_equal({"a": [1, 2.0]}, {"a": [1.0, 2]})
    assert not json_equal(True, 1)
    assert not json_equal({"a": 1}, {"a": 1, "b": None})


def test_merge_patch_follows_rfc_7386():
    target = {"a": "b", "c": {"d": "e", "f": "g"}}
    assert merge_patch(target, {"a": "z", "c": {"f": None}}) == {"a": "z", "c": {"d": "e"}}
    assert target == {"a": "b", "c": {"d": "e", "f": "g"}}


def test_exceeds_size_exits_early_on_large_values():
    assert exceeds_size(list(range(10**6)), 100)
    assert not exceeds_size({"a": [1, 2, 3]}, 100)
