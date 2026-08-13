"""
Soccer adapter and detector tests.

The title clinch is the interesting one. Like a career milestone it cannot be
judged from a single match — it needs the table walked matchday by matchday to
find when a lead became mathematically unassailable. And like a career
milestone, incomplete input produces a confident wrong answer rather than an
obvious failure.
"""

import pytest

from lambdas.common.notability import soccer as nb
from lambdas.common.sources import football_json as fj


def raw_match(date, home, away, hg, ag, rnd="Matchday 1"):
    return {"round": rnd, "date": date, "team1": home, "team2": away,
            "score": {"ft": [hg, ag]}}


def norm(date, home, away, hg, ag, season="2023-24", league="en.1"):
    return fj.normalize(raw_match(date, home, away, hg, ag),
                        league, "English Premier League", season)


class TestNormalize:
    def test_team1_is_home(self):
        """The export's convention, and getting it backwards inverts every result."""
        g = norm("2023-08-11", "Burnley FC", "Manchester City FC", 0, 3)
        assert g["home"]["team"] == "Burnley FC"
        assert g["away"]["team"] == "Manchester City FC"
        assert g["away"]["isWinner"] is True
        assert g["home"]["isWinner"] is False
        assert g["margin"] == 3
        assert g["combinedGoals"] == 3

    def test_draw_has_no_winner(self):
        g = norm("2023-08-11", "A FC", "B FC", 2, 2)
        assert g["isDraw"] is True
        assert g["home"]["isWinner"] is False
        assert g["away"]["isWinner"] is False

    def test_match_without_a_result_is_dropped(self):
        assert fj.normalize(
            {"date": "2024-01-01", "team1": "A", "team2": "B", "score": {}},
            "en.1", "EPL", "2023-24") is None

    def test_match_without_a_date_is_dropped(self):
        assert fj.normalize(
            {"team1": "A", "team2": "B", "score": {"ft": [1, 0]}},
            "en.1", "EPL", "2023-24") is None


class TestPerMatchDetectors:
    def test_ordinary_result_is_not_notable(self):
        assert nb.run([norm("2023-08-11", "A FC", "B FC", 2, 1)]) == []

    @pytest.mark.parametrize("hg,ag,fires", [
        (4, 0, False),   # 4-goal margin is common
        (6, 0, True),
        (6, 1, True),
    ])
    def test_big_win_threshold(self, hg, ag, fires):
        got = [e for e in nb.run([norm("2023-08-11", "A FC", "B FC", hg, ag)])
               if e["reason"] == "soccer_big_win"]
        assert bool(got) is fires

    def test_goal_fest(self):
        got = [e for e in nb.run([norm("2023-08-11", "A FC", "B FC", 5, 3)])
               if e["reason"] == "soccer_goal_fest"]
        assert got and got[0]["facts"]["combinedGoals"] == 8

    def test_high_scoring_draw(self):
        got = [e for e in nb.run([norm("2023-08-11", "A FC", "B FC", 3, 3)])
               if e["reason"] == "soccer_high_draw"]
        assert got and got[0]["facts"]["goalsEach"] == 3

    def test_a_one_one_draw_is_not_notable(self):
        assert nb.run([norm("2023-08-11", "A FC", "B FC", 1, 1)]) == []


class TestSeasonCompletenessGuard:
    """
    A clinch computed on a partial season is wrong, not approximate.

    The real export ships Serie A 2024-25 with 370 matches instead of 380 —
    every team a match short. That understates what the chasing team can still
    win, so the title fires days early with a confident, wrong date.
    """

    def _season(self, teams, drop_last=0):
        """A complete double round-robin, optionally missing trailing matches."""
        matches = []
        day = 1
        for i, home in enumerate(teams):
            for j, away in enumerate(teams):
                if i == j:
                    continue
                # Team 0 wins everything, so the title is decided early.
                hg, ag = (3, 0) if i == 0 else (0, 3) if j == 0 else (1, 1)
                matches.append(norm(f"2024-{(day % 12) + 1:02d}-"
                                    f"{(day % 28) + 1:02d}",
                                    home, away, hg, ag))
                day += 1
        matches.sort(key=lambda m: m["gameDate"])
        return matches[:-drop_last] if drop_last else matches

    def test_complete_season_yields_a_clinch(self):
        events = nb.detect_title_clinches(self._season(["A", "B", "C", "D"]))
        assert len(events) == 1
        assert events[0]["facts"]["champion"] == "A"

    def test_incomplete_season_yields_nothing(self):
        events = nb.detect_title_clinches(self._season(["A", "B", "C", "D"],
                                                       drop_last=3))
        assert events == [], "a partial season must not produce a clinch"

    def test_completeness_check_expects_a_double_round_robin(self):
        assert nb._season_is_complete({"A": 6, "B": 6, "C": 6, "D": 6}) is True
        assert nb._season_is_complete({"A": 6, "B": 6, "C": 6, "D": 5}) is False
        assert nb._season_is_complete({}) is False


class TestClinchMath:
    def test_a_lead_that_can_still_be_caught_is_not_a_clinch(self):
        """
        Three points a match means a six-point lead with two rounds left is not
        yet decided.
        """
        matches = [
            norm("2024-05-01", "A", "B", 1, 0),
            norm("2024-05-08", "A", "C", 1, 0),
            norm("2024-05-15", "B", "C", 1, 0),
        ]
        # Deliberately not a complete round-robin, so the guard suppresses it.
        assert nb.detect_title_clinches(matches) == []

    def test_clinch_is_attributed_to_the_day_not_the_match(self):
        """
        Several matches share a date. The clinch belongs to the date once every
        match on it has been counted, otherwise it lands on whichever fixture
        happened to sort last.
        """
        events = nb.detect_title_clinches(
            TestSeasonCompletenessGuard()._season(["A", "B", "C", "D"]))
        assert events
        e = events[0]
        assert len(e["gameDate"]) == 10
        assert e["mmdd"] == e["gameDate"][5:]


class TestOutputShape:
    def test_shared_event_shape(self):
        for e in nb.run([norm("2023-12-26", "A FC", "B FC", 6, 0)]):
            for key in ("sport", "league", "reason", "notabilityScore", "gameId",
                        "gameDate", "year", "mmdd", "title", "facts",
                        "sourceName", "sourceDatasetRef"):
                assert key in e, f"missing {key}"
            assert e["sport"] == "soccer"
            assert "None" not in e["title"]

    def test_attribution_is_public_domain(self):
        assert "CC0" in fj.ATTRIBUTION
