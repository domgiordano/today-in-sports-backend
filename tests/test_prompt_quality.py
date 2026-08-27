"""
Properties every generated prompt must hold, whatever template made it.

These are the defects players actually reported, expressed as rules over the
whole corpus rather than per-template assertions — a template added next month
gets checked by them for free.
"""

import re

import pytest

from lambdas.common.templates import phrasing


class TestCompetitionNames:
    @pytest.mark.parametrize("raw,expected", [
        ("English Premier League 2022/23", "English Premier League"),
        ("French Ligue 1 2021/22", "French Ligue 1"),
        ("Deutsche 2. Bundesliga 2012/13", "Deutsche 2. Bundesliga"),
        ("Netherlands Eredivisie 2021/22", "Netherlands Eredivisie"),
        ("Primera División de España 2023/24", "Primera División de España"),
        # Nothing to strip.
        ("MLB", "MLB"),
        ("NBA", "NBA"),
        (None, ""),
    ])
    def test_the_season_is_stripped_from_a_competition_name(self, raw, expected):
        """
        "2022/23" is how a database labels a row, not how anyone speaks, and it
        is redundant beside a prompt that already gives the date.
        """
        assert phrasing.competition(raw) == expected

    def test_a_year_inside_a_name_survives(self):
        """Stripping is anchored to the end, so a real name keeps its digits."""
        assert phrasing.competition("Copa América 2019") == "Copa América 2019"


class TestPhrasingIsStable:
    def test_the_same_question_always_reads_the_same_way(self):
        """
        A question's id is a hash of its own prompt. A prompt that varied
        between runs would mint a new id every time, orphaning its review
        status and the record of which dates have used it.
        """
        seed = ("game-1", "blowout", 7)
        first = phrasing.pick(["a", "b", "c", "d"], *seed)
        assert all(phrasing.pick(["a", "b", "c", "d"], *seed) == first
                   for _ in range(20))

    def test_different_questions_spread_across_the_variants(self):
        seen = {phrasing.pick(["a", "b", "c"], f"g{i}", "r", i) for i in range(90)}
        assert seen == {"a", "b", "c"}

    def test_it_refuses_an_empty_set_of_phrasings(self):
        with pytest.raises(ValueError):
            phrasing.pick([], "g", "r", 1)


class TestNoAnswerInThePrompt:
    """
    13% of numeric questions stated a scoreline and then asked for something
    derivable from it — a subtraction test with a sports fact attached.
    """

    def _numbers(self, prompt):
        return [float(n) for n in re.findall(r"\b\d+\b", prompt)]

    def _leaks(self, prompt, answer):
        nums = self._numbers(prompt)
        if answer in nums:
            return "stated outright"
        for i, x in enumerate(nums):
            for y in nums[i + 1:]:
                if abs(x - y) == answer:
                    return "a difference of two numbers given"
                if x + y == answer:
                    return "a sum of two numbers given"
        return None

    def test_the_baseball_blowout_question_does_not_state_its_answer(self):
        from lambdas.common.templates import mlb_templates as tpl

        event = {
            "reason": "blowout", "gameId": "g1", "gameDate": "2018-04-07",
            "year": 2018, "mmdd": "04-07", "sport": "mlb", "league": "MLB",
            "sourceName": "retrosheet", "sourceDatasetRef": "r",
            "notabilityScore": 80,
            "facts": {"scoringTeam": "Philadelphia Phillies",
                      "opponent": "Miami Marlins", "runs": 20, "opponentRuns": 1},
        }
        [q] = tpl.numeric_blowout_margin(event, {})
        assert self._leaks(q["prompt"], float(q["numericAnswer"])) is None, q["prompt"]

    def test_the_basketball_blowout_question_does_not_state_its_answer(self):
        from lambdas.common.templates import winter_templates as tpl

        event = {
            "sport": "nba", "league": "NBA", "reason": "nba_blowout",
            "gameId": "g2", "gameDate": "2020-08-29", "mmdd": "08-29",
            "year": 2020, "sourceName": "balldontlie", "sourceDatasetRef": "r",
            "facts": {"winningTeam": "Houston Rockets",
                      "losingTeam": "Oklahoma City Thunder",
                      "winningScore": 114, "losingScore": 80, "margin": 34},
        }
        [q] = tpl.nba_blowout_margin(event, {})
        assert self._leaks(q["prompt"], float(q["numericAnswer"])) is None, q["prompt"]

    def test_the_soccer_goal_fest_question_gives_the_player_an_anchor(self):
        """
        It used to say only that the match was "remarkable", which is no route
        to the answer at all. One side's score is both an anchor and not the
        thing being asked for.
        """
        from lambdas.common.templates import winter_templates as tpl

        event = {
            "sport": "soccer", "league": "EPL", "reason": "soccer_goal_fest",
            "gameId": "g3", "gameDate": "2022-08-27", "mmdd": "08-27",
            "year": 2022, "sourceName": "football_json", "sourceDatasetRef": "r",
            "facts": {"homeTeam": "Liverpool FC", "awayTeam": "AFC Bournemouth",
                      "homeScore": 9, "awayScore": 0, "combinedGoals": 9,
                      "competition": "English Premier League 2022/23"},
        }
        [q] = tpl.soccer_goal_fest_total(event, {})
        assert "remarkable" not in q["prompt"]
        assert "2022/23" not in q["prompt"]
        assert "9" in q["prompt"], "the anchor has to actually be there"
        assert self._leaks(q["prompt"], float(q["numericAnswer"])) is None, q["prompt"]
