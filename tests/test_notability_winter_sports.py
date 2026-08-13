"""
Detector tests for the sports that fill the calendar where baseball cannot.

Baseball produces nothing between November and February — five months, roughly
120 calendar dates. Hockey, football and motorsport are what cover them, so
these detectors are load-bearing for coverage, not just for breadth.

Every case is checkable against the historical record. Fixtures are real rows
captured from each source; no test touches the network.
"""

import json
import os

import pytest

from lambdas.common.notability import f1 as f1_nb
from lambdas.common.notability import nfl as nfl_nb
from lambdas.common.notability import nhl as nhl_nb

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


# --------------------------------------------------------------------- NHL

class TestNHL:
    @pytest.fixture
    def games(self):
        return load("nhl_games.json")

    def test_cup_clinchers_are_detected(self, games):
        events = nhl_nb.run(games)
        clinchers = [e for e in events if e["reason"] == "stanley_cup_clincher"]
        assert clinchers, "no Cup clincher found in the fixture"
        for e in clinchers:
            assert "won the Stanley Cup" in e["title"]
            assert e["facts"]["winningTeam"]
            assert e["notabilityScore"] == 98

    def test_clincher_requires_reaching_the_wins_needed(self):
        """
        A Final game that does not end the series is not a clincher. Derived
        from the series tally rather than assumed from the round.
        """
        base = {
            "sport": "nhl", "gameId": 1, "gameDate": "1994-06-09",
            "season": 19931994, "gameType": 3, "status": "OFF",
            "seriesDescription": "Stanley Cup Final", "seriesAbbrev": "SCF",
            "seriesGameNumber": 5, "neededToWin": 4,
            "topSeed": "NYR", "topSeedWins": 3,
            "bottomSeed": "VAN", "bottomSeedWins": 2,
            "periodType": "REG", "periods": 3, "venue": "MSG",
            "away": {"team": "Vancouver Canucks", "teamId": "VAN", "score": 3,
                     "isWinner": False, "league": "NHL"},
            "home": {"team": "New York Rangers", "teamId": "NYR", "score": 6,
                     "isWinner": True, "league": "NHL"},
            "sourceName": "nhl-api", "sourceDatasetRef": "x",
        }
        events = nhl_nb.run([base])
        assert not [e for e in events if e["reason"] == "stanley_cup_clincher"]

        # Same game, but now the win completes the series.
        base["topSeedWins"] = 4
        events = nhl_nb.run([base])
        assert [e for e in events if e["reason"] == "stanley_cup_clincher"]

    def test_regular_season_games_produce_nothing(self):
        """Only 15+ goal floods should escape the regular season."""
        g = {
            "sport": "nhl", "gameId": 2, "gameDate": "1975-01-15",
            "season": 19741975, "gameType": 2, "status": "OFF",
            "seriesDescription": "Regular Season", "seriesAbbrev": None,
            "seriesGameNumber": None, "neededToWin": None,
            "periodType": "REG", "periods": 3,
            "away": {"team": "Boston Bruins", "teamId": "BOS", "score": 3,
                     "isWinner": False, "league": "NHL"},
            "home": {"team": "Montreal Canadiens", "teamId": "MTL", "score": 4,
                     "isWinner": True, "league": "NHL"},
            "sourceName": "nhl-api", "sourceDatasetRef": "x",
        }
        assert nhl_nb.run([g]) == []

    def test_one_event_per_game(self, games):
        """A Cup-clinching Game 7 in overtime legitimately trips three rules."""
        events = nhl_nb.run(games)
        ids = [e["gameId"] for e in events]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------- F1

class TestF1:
    @pytest.fixture
    def races(self):
        return load("f1_races.json")

    def test_first_championship_race_is_a_debut_win(self, races):
        """1950-05-13 Silverstone: every driver's first championship start."""
        events = f1_nb.run(races)
        e = next(x for x in events if x["gameDate"] == "1950-05-13")
        assert e["reason"] == "debut_win"
        assert "Farina" in e["title"]

    @pytest.mark.parametrize("date,winner", [
        ("2008-11-02", "Massa"),      # Hamilton took the title; Massa won the race
        ("2021-12-12", "Verstappen"),
    ])
    def test_championship_deciders(self, races, date, winner):
        events = f1_nb.run(races)
        e = next(x for x in events if x["gameDate"] == date)
        assert e["reason"] == "championship_decider"
        assert e["facts"]["decidedDriversTitle"] is True
        # The wording must credit the race win, not the title — in 2008 those
        # were different drivers.
        assert "won the race" in e["title"]
        assert winner in e["title"]

    def test_a_race_with_no_winner_produces_nothing(self):
        """Scheduled future races are present in the dump with no result."""
        r = {
            "sport": "f1", "gameId": "future", "gameDate": "2026-12-06",
            "year": 2026, "round": 24, "grandPrix": "Abu Dhabi Grand Prix",
            "officialName": "FORMULA 1 ABU DHABI GRAND PRIX 2026",
            "championshipDecider": False, "winner": None, "podium": [],
            "sourceName": "f1db", "sourceDatasetRef": "x",
        }
        assert f1_nb.run([r]) == []


# --------------------------------------------------------------------- NFL

class TestNFL:
    @pytest.fixture
    def games(self):
        return load("nfl_games.json")

    @pytest.mark.parametrize("date,number,champion", [
        ("2002-02-03", 36, "New England Patriots"),
        ("2018-02-04", 52, "Philadelphia Eagles"),
        ("2025-02-09", 59, "Philadelphia Eagles"),
    ])
    def test_super_bowl_number_is_derived_correctly(self, games, date, number, champion):
        """
        The dataset starts at 1999 and carries no Super Bowl number, so it is
        derived from the season. Getting the arithmetic wrong would misname
        every single one.
        """
        events = nfl_nb.run(games)
        e = next(x for x in events if x["gameDate"] == date)
        assert e["reason"] == "super_bowl"
        assert e["facts"]["superBowlNumber"] == number
        assert e["facts"]["winningTeam"] == champion
        assert f"Super Bowl {number}" in e["title"]

    def test_regular_season_game_is_not_notable(self, games):
        reg = [g for g in games if g["gameType"] == "REG"]
        assert reg, "fixture should include a control regular-season game"
        assert nfl_nb.run(reg) == []


# ------------------------------------------------------- cross-sport shape

class TestSharedShape:
    """
    The assembler and templates consume every sport through the same event
    shape, so a missing key is a downstream break rather than a local one.
    """

    REQUIRED = {"sport", "league", "reason", "notabilityScore", "gameId",
                "gameDate", "year", "mmdd", "title", "facts",
                "sourceName", "sourceDatasetRef"}

    def test_every_sport_emits_the_same_keys(self):
        events = (
            nhl_nb.run(load("nhl_games.json"))
            + f1_nb.run(load("f1_races.json"))
            + nfl_nb.run(load("nfl_games.json"))
        )
        assert events
        for e in events:
            missing = self.REQUIRED - set(e)
            assert not missing, f"{e['sport']}/{e['reason']} missing {missing}"
            assert len(e["mmdd"]) == 5 and e["mmdd"][2] == "-"
            assert 1 <= int(e["mmdd"][:2]) <= 12
            assert e["sourceDatasetRef"], "provenance is mandatory"
            assert "None" not in e["title"]


class TestTemplatesDoNotCrossSports:
    """
    Reason codes are not unique across sports — NHL and NFL both use
    `playoff_overtime`. Templates keyed on reason alone fired the football
    template on hockey events, producing two near-identical questions from one
    game. Every template must gate on sport as well.
    """

    def test_no_event_yields_a_question_from_another_sport(self):
        from lambdas.common.templates import winter_templates as wt

        events = (
            nhl_nb.run(load("nhl_games.json"))
            + f1_nb.run(load("f1_races.json"))
            + nfl_nb.run(load("nfl_games.json"))
        )
        by_id = {e["gameId"]: e["sport"] for e in events}

        for q in wt.generate(events):
            assert q["sport"] == by_id[q["sourceEventId"]], (
                f"{q['sport']} template fired on a "
                f"{by_id[q['sourceEventId']]} event: {q['prompt'][:60]}"
            )

    def test_one_question_per_template_per_event(self):
        from lambdas.common.templates import winter_templates as wt

        events = nhl_nb.run(load("nhl_games.json"))
        prompts = [q["prompt"] for q in wt.generate(events)]
        assert len(prompts) == len(set(prompts)), "duplicate prompts generated"
