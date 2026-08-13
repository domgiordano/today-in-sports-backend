"""
Retrosheet adapter tests.

Field positions in a 161-column fixed-layout record are exactly the kind of
thing that is silently wrong, so these check parsed values against events
verifiable in the historical record rather than against the parser's own output.

Fixtures are trimmed real game-log rows in tests/fixtures/retrosheet_*.csv.
"""

import csv
import os

import pytest

from lambdas.common.notability import mlb as nb
from lambdas.common.sources import retrosheet as rs

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def names():
    """Minimal code -> name table covering the fixture teams."""
    from datetime import date
    return {
        "TOR": [{"league": "AL", "name": "Toronto Blue Jays",
                 "start": date(1977, 4, 7), "end": None}],
        "TEX": [{"league": "AL", "name": "Texas Rangers",
                 "start": date(1972, 4, 15), "end": None}],
        "ATL": [{"league": "NL", "name": "Atlanta Braves",
                 "start": date(1966, 4, 12), "end": None}],
        "MIN": [{"league": "AL", "name": "Minnesota Twins",
                 "start": date(1961, 4, 11), "end": None}],
        "BAL": [{"league": "AL", "name": "Baltimore Orioles",
                 "start": date(1954, 4, 13), "end": None}],
        "OAK": [{"league": "AL", "name": "Oakland Athletics",
                 "start": date(1968, 4, 10), "end": None}],
        # Two windows for the same code — the 1920 game must resolve to Robins.
        "BRO": [
            {"league": "NL", "name": "Brooklyn Robins",
             "start": date(1914, 4, 14), "end": date(1931, 9, 27)},
            {"league": "NL", "name": "Brooklyn Dodgers",
             "start": date(1932, 4, 12), "end": date(1957, 9, 29)},
        ],
    }


def load_rows(name):
    with open(os.path.join(FIXTURES, f"retrosheet_{name}.csv"),
              newline="", encoding="latin-1") as f:
        return [r for r in csv.reader(f) if len(r) >= 161]


class TestInningsFromOuts:
    """
    Length is recorded in outs, not innings, and the conversion is not a plain
    divide: 51 outs is a nine-inning game the home team won without batting in
    the ninth.
    """

    @pytest.mark.parametrize("outs,expected", [
        (54, 9),   # full nine
        (51, 9),   # home team never batted in the ninth
        (58, 10),  # 1991 World Series Game 7
        (71, 12),  # 1991 World Series Game 3
        (60, 10),
    ])
    def test_conversion(self, outs, expected):
        assert rs.innings_from_outs(outs) == expected

    def test_missing_is_none(self):
        assert rs.innings_from_outs("") is None
        assert rs.innings_from_outs(None) is None


class TestTeamNames:
    def test_resolves_name_in_use_at_the_time(self, names):
        from datetime import date
        assert rs.team_name(names, "BRO", date(1920, 10, 10)) == "Brooklyn Robins"
        assert rs.team_name(names, "BRO", date(1955, 9, 1)) == "Brooklyn Dodgers"

    def test_unknown_code_resolves_to_nothing_rather_than_the_code(self, names):
        """
        Falling back to the raw code is what produced "the CL4 routed the
        Pittsburgh Alleghenys" — CurrentNames.csv does not cover every club that
        ever played. A missing name is a game to skip, not a name to invent.
        """
        from datetime import date
        assert rs.team_name(names, "ZZZ", date(1991, 5, 1)) is None

    @pytest.mark.parametrize("value,is_code", [
        ("CL4", True),
        ("PIT", True),
        ("", True),
        (None, True),
        ("Pittsburgh Alleghenys", False),
        ("Ajax", False),
    ])
    def test_raw_codes_are_recognised(self, value, is_code):
        assert rs.looks_like_a_raw_code(value) is is_code


class TestNormalize:
    def test_ryan_no_hitter_parses_correctly(self, names):
        """1991-05-01, TOR 0 @ TEX 3 — Nolan Ryan's seventh."""
        row = load_rows("1991-05-01")[0]
        g = rs.normalize(row, names)

        assert g["gameDate"] == "1991-05-01"
        assert g["away"]["team"] == "Toronto Blue Jays"
        assert g["home"]["team"] == "Texas Rangers"
        assert g["away"]["hits"] == 0
        assert g["away"]["runs"] == 0
        assert g["home"]["runs"] == 3
        assert g["home"]["isWinner"] is True
        assert g["innings"] == 9
        assert g["decisions"]["winner"]["fullName"] == "Nolan Ryan"

    def test_pitchers_used_is_read_from_the_record(self, names):
        """
        Field 67 answers solo-versus-combined with no follow-up request. Ryan
        threw a complete game, so exactly one.
        """
        g = rs.normalize(load_rows("1991-05-01")[0], names)
        assert g["home"]["pitchersUsed"] == 1

    def test_walks_prevent_a_false_perfect_game(self, names):
        """Ryan walked two — a no-hitter, never a perfect game."""
        g = rs.normalize(load_rows("1991-05-01")[0], names)
        assert g["away"]["walks"] == 2
        assert g["away"]["atBats"] == 27


class TestDetectorsRunUnchangedOnRetrosheet:
    """
    The whole point of the shared normalized shape: one detector library, two
    sources, identical verdicts.
    """

    def test_no_hitter_detected_and_attributed_solo(self, names):
        g = rs.normalize(load_rows("1991-05-01")[0], names)
        events = nb.run([g], enrich=False)
        nh = [e for e in events if e["reason"] == "no_hitter"]

        assert len(nh) == 1
        f = nh[0]["facts"]
        assert f["noHitTeam"] == "Toronto Blue Jays"
        assert f["pitcher"] == "Nolan Ryan"
        assert f["combined"] is False
        assert f["pitchersUsed"] == 1
        assert f["attributionConfidence"] == "high"
        # Two walks, so it must not have been promoted to a perfect game.
        assert nh[0]["reason"] == "no_hitter"

    def test_combined_no_hitter_is_credited_to_the_staff(self, names):
        """1991-07-13 — Milacki, Flanagan, Williamson and Olson."""
        g = rs.normalize(load_rows("1991-07-13")[0], names)
        nh = [e for e in nb.run([g], enrich=False) if e["reason"] == "no_hitter"]

        assert len(nh) == 1
        f = nh[0]["facts"]
        assert f["pitchersUsed"] == 4
        assert f["combined"] is True
        assert f["pitcher"] is None
        assert f["attributionConfidence"] == "combined"
        assert "pitching staff" in f["creditedTo"]

    def test_world_series_game_seven(self, names):
        """1991-10-27 — ATL 0 @ MIN 1 in ten innings."""
        row = load_rows("1991-10-27")[0]
        g = rs.normalize(row, names, series="World Series",
                         game_number=7, games_in_series=7)
        assert g["innings"] == 10
        assert g["seriesGameNumber"] == 7

        events = nb.run([g], enrich=False)
        assert events, "a World Series Game 7 must produce an event"
        top = events[0]
        assert top["facts"]["winningTeam"] == "Minnesota Twins"


class TestProvenance:
    def test_every_game_carries_source_identification(self, names):
        for fixture in ("1991-05-01", "1991-07-13", "1991-10-27"):
            g = rs.normalize(load_rows(fixture)[0], names)
            assert g["sourceName"] == "retrosheet"
            assert g["sourceDatasetRef"], "a game without provenance is unusable"
            assert "retrosheet.org" in g["sourceDatasetRef"]

    def test_attribution_notice_is_present_and_exact(self):
        """The licence requires this wording; paraphrasing it is a violation."""
        assert "obtained free of charge" in rs.ATTRIBUTION
        assert "copyrighted by Retrosheet" in rs.ATTRIBUTION
        assert "www.retrosheet.org" in rs.ATTRIBUTION
