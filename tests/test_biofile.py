"""
The biographical file, and the one era it exists for.
"""

import pytest

from lambdas.common.sources import biofile


def test_the_name_a_player_was_known_by_beats_the_name_on_the_certificate():
    """
    FIRST holds the full given name, so building from it yields "Robert Lee
    Caruthers" - correct, and not what any player would be answered as. The
    known-by name lives in NICKNAME.
    """
    index = biofile._index_rows([
        {"PLAYERID": "carub101", "LAST": "Caruthers",
         "FIRST": "Robert Lee", "NICKNAME": "Bob"},
        {"PLAYERID": "clarj102", "LAST": "Clarkson",
         "FIRST": "John Gibson", "NICKNAME": ""},
    ])
    assert index["carub101"] == "Bob Caruthers"
    # No nickname: the first token only, never the middle name.
    assert index["clarj102"] == "John Clarkson"


def test_a_player_with_no_surname_is_not_indexed():
    index = biofile._index_rows([{"PLAYERID": "x", "LAST": "", "FIRST": "Al"}])
    assert index == {}


def test_an_unknown_id_keeps_the_name_the_log_gave():
    """
    35 pre-1900 players have no first name on record anywhere. A surname still
    beats nothing, so the caller's name survives.
    """
    assert biofile.display_name({}, "nobody001", fallback="Keefe") == "Keefe"
    assert biofile.display_name({}, "", fallback="Keefe") == "Keefe"


@pytest.mark.parametrize("name,incomplete", [
    ("Keefe", True),
    ("Tim Keefe", False),
    ("", False),          # nothing to complete
    ("  Galvin  ", True),
])
def test_only_a_bare_surname_asks_for_a_lookup(name, incomplete):
    """
    The lookup must never fire on a name the log already got right - replacing
    a complete name risks swapping in a different player entirely.
    """
    assert biofile.looks_incomplete(name) is incomplete


def test_the_lookup_reaches_the_game_log_it_was_written_for():
    """
    The wiring, not just the lookup. Every unit test passed while the import
    was missing from retrosheet.py, because none of them ran _players with an
    index - so the corpus build died on the first 1876 game instead.
    """
    from lambdas.common.sources import retrosheet as rs

    row = [""] * 200
    row[rs.F["date"]] = "18800501"
    row[rs.F["vStartPitcherId"]] = "keeft101"
    row[rs.F["vStartPitcher"]] = "Keefe"
    row[rs.F["hStartPitcherId"]] = "clemr001"
    row[rs.F["hStartPitcher"]] = "Roger Clemens"

    people = rs._players(row, "1880-05-01", {"keeft101": "Tim Keefe"})
    assert people["awayStarter"]["name"] == "Tim Keefe"
    # A name the log already got right is never replaced.
    assert people["homeStarter"]["name"] == "Roger Clemens"

    # And with no index at all, nothing changes.
    assert rs._players(row, "1880-05-01")["awayStarter"]["name"] == "Keefe"
