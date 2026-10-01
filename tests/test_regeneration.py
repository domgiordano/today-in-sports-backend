"""
The regeneration definition, and the coupling it exists to hold together.

`regenerate_questions` writes the new wording; `prune_superseded` removes what
the new wording replaced. A row is superseded exactly when its slot is one the
templates still produce and its id is not among the ids they produce — both
halves of which have to mean the same thing in both scripts.

They each used to carry their own copy. A clue-ladder rewrite was added to one
and not the other, and the prune then judged 6,186 rewritten questions as "not
produced by the current templates", retiring 23 rows where 6,186 were stale.
"""

import pathlib
import re

import pytest

from lambdas.common import regeneration

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
COUPLED = ("regenerate_questions.py", "prune_superseded.py")


@pytest.mark.parametrize("script", COUPLED)
def test_the_coupled_scripts_do_not_redefine_the_definition(script):
    """
    The failure was two copies drifting, so the test is that there is one copy.
    A script that assigns these names again has forked the definition, whatever
    the values happen to be on the day it is written.
    """
    source = (SCRIPTS / script).read_text()
    for name in ("WINTER_SPORTS", "TRANSACTION_REASONS", "MLB_CONTEXT_FREE"):
        assert not re.search(rf"^{name}\s*=", source, re.M), (
            f"{script} redefines {name}; it belongs in lambdas/common/regeneration.py "
            f"so both scripts cannot disagree about it")


@pytest.mark.parametrize("script", COUPLED)
def test_the_coupled_scripts_call_the_shared_regenerator(script):
    source = (SCRIPTS / script).read_text()
    assert "regeneration.regenerate(" in source, (
        f"{script} builds its own question set instead of using the shared one")


class TestRegenerate:
    def _event(self, game_id="g1", winner="Liverpool FC", loser="Norwich City FC", **kw):
        base = {
            "sport": "soccer", "league": "EPL", "leagueId": "en.1",
            "reason": "soccer_big_win", "gameId": game_id,
            "gameDate": "2019-08-09", "mmdd": "08-09", "year": 2019,
            "sourceName": "football_json", "sourceDatasetRef": "r",
            "facts": {"winningTeam": winner, "losingTeam": loser,
                      "winningScore": 6, "losingScore": 0, "margin": 6,
                      "competition": "English Premier League 2019/20"},
        }
        base.update(kw)
        return base

    def _events(self):
        """One event to ask about, and a season of clubs to draw distractors from."""
        return [self._event(),
                self._event("g2", "Manchester City FC", "Watford FC"),
                self._event("g3", "Chelsea FC", "Everton FC")]

    def test_it_produces_questions_from_events_alone(self):
        """No source archive, no games table — the events are the input."""
        out = regeneration.regenerate(self._events())
        assert {q.get("sourceEventId") for q in out} == {"g1", "g2", "g3"}

    def test_every_question_lands_in_a_slot_it_reports(self):
        """
        The invariant the prune depends on: anything regenerate produces is in
        a slot regenerate claims, or the prune would delete it as superseded by
        itself.
        """
        out = regeneration.regenerate(self._events())
        slots = regeneration.slots(out)
        assert all((q.get("sourceEventId"), q.get("type")) in slots for q in out)

    def test_an_unknown_sport_is_ignored_rather_than_guessed_at(self):
        assert regeneration.regenerate([self._event(sport="cricket")]) == []

    def test_it_is_stable_across_runs(self):
        """
        Ids hash the prompt, so an unstable phrasing choice would mint new
        questions on every run and supersede the ones written a moment earlier.
        """
        first = {q["questionId"] for q in regeneration.regenerate(self._events())}
        second = {q["questionId"] for q in regeneration.regenerate(self._events())}
        assert first == second


class TestRetired:
    """
    The score-arithmetic questions no template makes any more. Nothing
    regenerates their slot, so without this the prune could never see them and
    they would stay approved in the bank, still winning quiz slots.
    """

    @pytest.mark.parametrize("sport,reason", [
        ("soccer", "soccer_big_win"), ("soccer", "soccer_goal_fest"),
        ("nfl", "regular_season_blowout"), ("nfl", "super_bowl"),
        ("nba", "nba_low_score"), ("mlb", "blowout"), ("mlb", "postseason_shutout"),
    ])
    def test_a_score_question_is_retired(self, sport, reason):
        assert regeneration.retired({"type": "numeric", "sport": sport, "sourceReason": reason})

    @pytest.mark.parametrize("q", [
        # Same event, different format: the replacement itself.
        {"type": "mc", "sport": "soccer", "sourceReason": "soccer_big_win"},
        # Numeric, but not a score: innings and title-race arithmetic stay.
        {"type": "numeric", "sport": "mlb", "sourceReason": "extra_innings_marathon"},
        {"type": "numeric", "sport": "soccer", "sourceReason": "soccer_title_clinched"},
        # Reason codes are not unique across sports.
        {"type": "numeric", "sport": "nhl", "sourceReason": "blowout"},
    ])
    def test_everything_else_is_not(self, q):
        assert not regeneration.retired(q)
