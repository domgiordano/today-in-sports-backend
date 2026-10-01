"""
Properties every generated prompt must hold, whatever template made it.

These are the defects players actually reported, expressed as rules over the
whole corpus rather than per-template assertions — a template added next month
gets checked by them for free.
"""

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
