"""
"On this day" history questions.

The rules that matter: every claim in a prompt or reveal is read off the event,
the answer is one of the options exactly once, and the three wrong answers are
real, distinct clubs from the same competition and era. A record is claimed
only where the corpus can prove it.
"""

import json
import re

import pytest

from lambdas.common import play_view
from lambdas.common.templates import history_templates as tpl
from lambdas.common.templates.mlb_templates import validate


def ev(sport, reason, facts, game_id="g1", year=2019, **kw):
    base = {"sport": sport, "league": sport.upper(), "leagueId": f"{sport}.1",
            "reason": reason, "gameId": game_id, "gameDate": f"{year}-10-01",
            "mmdd": "10-01", "year": year, "notabilityScore": 80,
            "sourceName": "src", "sourceDatasetRef": "https://example.org/row",
            "facts": facts}
    base.update(kw)
    return base


COMP = "English Premier League 2019/20"


def soccer(game_id, winner, loser, w=6, l=0, reason="soccer_big_win"):
    return ev("soccer", reason, {"winningTeam": winner, "losingTeam": loser,
                                 "winningScore": w, "losingScore": l,
                                 "margin": w - l, "competition": COMP}, game_id)


SEASON = [soccer("g2", "Manchester City FC", "Watford FC"),
          soccer("g3", "Chelsea FC", "Everton FC"),
          soccer("g4", "Arsenal FC", "Burnley FC")]


def nfl(game_id, winner, loser, w, l, season=2018, reason="regular_season_blowout",
        rnd="Regular Season"):
    return ev("nfl", reason, {"winningTeam": winner, "losingTeam": loser,
                              "winningScore": w, "losingScore": l, "margin": w - l,
                              "combinedPoints": w + l, "season": season,
                              "round": rnd}, game_id, year=season)


SUPER_BOWL_2018 = nfl("sb", "New England Patriots", "Los Angeles Rams", 13, 3,
                      reason="super_bowl", rnd="Super Bowl")


def build(target, others):
    events = [target, *others]
    return tpl.generate([target], tpl.build_context(events))


def numbers(text):
    return {int(n) for n in re.findall(r"\b\d+\b", text)}


def assert_well_formed(q, event):
    options = [q["answer"], *q["distractors"]]
    assert validate(q) == [], validate(q)
    assert len(q["distractors"]) == 3
    assert len({tpl._norm(o) for o in options}) == 4, options
    assert q["answer"] not in q["prompt"]
    assert q["reveal"] and q["answer"] in q["reveal"]
    assert q["sourceDatasetRef"] == event["sourceDatasetRef"]
    named = {v for v in event["facts"].values() if isinstance(v, str)}
    # The side named in the prompt is never offered as a wrong answer.
    assert not named & set(q["distractors"])


class TestEveryClaimComesFromTheEvent:
    def test_a_soccer_rout_asks_who_and_states_only_the_row(self):
        event = soccer("g1", "Liverpool FC", "Norwich City FC", 7, 0)
        [q] = build(event, SEASON)
        assert q["type"] == "mc" and q["answer"] == "Liverpool FC"
        assert "on this day in 2019" in q["prompt"]
        assert "Norwich City FC" in q["prompt"]
        # Every number said aloud is the score, the margin, or the year.
        assert numbers(q["prompt"] + q["reveal"]) <= {7, 0, 2019, 20}
        assert "2019/20 English Premier League season" in q["reveal"]
        assert_well_formed(q, event)

    @pytest.mark.parametrize("home,away,answer", [(5, 4, "Leeds United"), (3, 6, "Fulham FC"),
                                                  (4, 4, "Fulham FC")])
    def test_a_goal_fest_asks_for_the_side_the_prompt_leaves_out(self, home, away, answer):
        event = ev("soccer", "soccer_goal_fest",
                   {"homeTeam": "Leeds United", "awayTeam": "Fulham FC",
                    "homeScore": home, "awayScore": away,
                    "combinedGoals": home + away, "competition": COMP})
        [q] = build(event, SEASON)
        assert q["answer"] == answer
        assert f"{home + away} goals" in q["reveal"]
        assert_well_formed(q, event)

    def test_baseball_and_hockey_name_the_winner(self):
        mlb_pool = [ev("mlb", "blowout", {"scoringTeam": t, "opponent": "Boston Red Sox",
                                          "runs": 20, "opponentRuns": 1}, f"m{i}", 2008)
                    for i, t in enumerate(["New York Yankees", "Tampa Bay Rays",
                                           "Toronto Blue Jays"])]
        event = ev("mlb", "slugfest", {"awayTeam": "Texas Rangers", "homeTeam": "Baltimore Orioles",
                                       "awayRuns": 30, "homeRuns": 3, "combinedRuns": 33},
                   year=2007)
        [q] = build(event, mlb_pool)
        assert q["answer"] == "Texas Rangers"
        assert "30-3" in q["prompt"]
        assert_well_formed(q, event)

    def test_a_world_series_game_is_a_who_won(self):
        pool = [ev("mlb", "blowout", {"scoringTeam": t, "opponent": "Boston Red Sox",
                                      "runs": 20, "opponentRuns": 1}, f"m{i}", 2026)
                for i, t in enumerate(["New York Yankees", "Tampa Bay Rays", "Toronto Blue Jays"])]
        event = ev("mlb", "world_series_game7",
                   {"winningTeam": "Los Angeles Dodgers", "losingTeam": "Toronto Blue Jays",
                    "winningRuns": 5, "losingRuns": 4, "gameNumber": 7}, year=2025)
        [q] = build(event, pool)
        assert q["answer"] == "Los Angeles Dodgers"
        assert "the deciding Game 7" in q["reveal"]
        assert "Toronto Blue Jays" not in q["distractors"]


class TestNothingIsGuessed:
    def test_a_missing_score_asks_nothing(self):
        event = soccer("g1", "Liverpool FC", "Norwich City FC")
        event["facts"]["losingScore"] = None
        assert build(event, SEASON) == []

    def test_a_pool_too_thin_for_three_distractors_asks_nothing(self):
        assert build(soccer("g1", "Liverpool FC", "Norwich City FC"), SEASON[:1]) == []

    @pytest.mark.parametrize("w,l", [(0, 0), (19, 0), (18, 17)])
    def test_basketball_scores_nobody_recorded_ask_nothing(self, w, l):
        """
        balldontlie holds 1,400+ "low-scoring" games, nearly all 0-0. Nothing
        below the 19-18 game of 1950 ever happened.
        """
        event = ev("nba", "nba_low_score", {"winningTeam": "Boston Celtics",
                                            "losingTeam": "New York Knicks",
                                            "winningScore": w, "losingScore": l})
        assert tpl.nba_history(event, {"nba_franchises": {}, "nba_by_season": {}}) == []

    def test_retrosheet_codes_are_not_clubs(self):
        event = ev("mlb", "blowout", {"scoringTeam": "NY2", "opponent": "ELI",
                                      "runs": 20, "opponentRuns": 3}, year=1872)
        assert tpl.mlb_blowout(event, tpl.build_context([event])) == []

    def test_all_star_sides_are_not_clubs(self):
        event = ev("nhl", "goal_flood", {"homeTeam": "ASW", "awayTeam": "CEN",
                                         "homeScore": 12, "awayScore": 11})
        assert tpl.nhl_goal_flood(event, tpl.build_context([event])) == []

    def test_one_club_under_two_spellings_is_never_both_answer_and_distractor(self):
        """
        openfootball calls one club "Real Sociedad" and "Real Sociedad de
        Fútbol". Offering both makes two options correct.
        """
        picks = tpl._distractors("Real Sociedad", ["Real Betis"],
                                 [["Real Sociedad de Fútbol", "Real Betis Balompié",
                                   "Sevilla FC", "Valencia CF", "Getafe CF"]], "seed")
        assert picks and not any("Sociedad" in p or "Betis" in p for p in picks)


class TestNflRecords:
    """
    The one superlative the events can prove: every regular-season game past
    the detector threshold is an event carrying its score, so the season's
    events past it are all of its games past it.
    """

    def _others(self):
        return [SUPER_BOWL_2018,
                nfl("o1", "Chicago Bears", "Arizona Cardinals", 40, 3),
                nfl("o2", "Kansas City Chiefs", "Oakland Raiders", 45, 5),
                nfl("o3", "Dallas Cowboys", "Detroit Lions", 30, 10, reason="one_point_game")]

    def test_the_season_record_is_claimed_when_it_is_provable(self):
        event = nfl("g1", "Baltimore Ravens", "Buffalo Bills", 47, 3)
        [q] = build(event, self._others())
        assert "the biggest winning margin of the 2018 regular season" in q["prompt"]
        assert "the biggest winning margin of the 2018 regular season" in q["reveal"]
        assert_well_formed(q, event)

    def test_a_game_that_is_not_the_record_says_how_rare_it_was(self):
        event = nfl("g1", "Baltimore Ravens", "Buffalo Bills", 38, 3)
        [q] = build(event, self._others())
        assert "biggest" not in q["prompt"] + q["reveal"]
        # This game, 37 points by the Bears and 40 by the Chiefs: three past 35.
        assert "one of 3 regular-season games in 2018 decided by 35 points or more" in q["reveal"]

    def test_a_shared_record_is_not_claimed_as_the_record(self):
        event = nfl("g1", "Baltimore Ravens", "Buffalo Bills", 43, 3)
        [q] = build(event, self._others())
        assert "biggest" not in q["prompt"] + q["reveal"]

    def test_a_season_without_its_super_bowl_claims_nothing(self):
        """The season in progress: a record now may not be one in January."""
        event = nfl("g1", "Baltimore Ravens", "Buffalo Bills", 47, 3, season=2026)
        others = [nfl(f"o{i}", t, "Buffalo Bills", 20, 10, season=2026)
                  for i, t in enumerate(["Chicago Bears", "Kansas City Chiefs", "Dallas Cowboys"])]
        [q] = build(event, others)
        assert "biggest" not in q["prompt"] + q["reveal"]
        assert "one of" not in q["reveal"]
        assert q["reveal"] == "Baltimore Ravens 47-3 Buffalo Bills: a 44-point win."

    def test_the_lowest_scoring_game_counts_downwards(self):
        event = nfl("g1", "Pittsburgh Steelers", "Miami Dolphins", 3, 0, reason="rock_fight")
        others = self._others() + [nfl("o4", "Seattle Seahawks", "Denver Broncos", 10, 3,
                                       reason="rock_fight")]
        [q] = build(event, others)
        assert "the lowest-scoring regular-season game of 2018" in q["prompt"]

    def test_a_game_passed_twice_is_counted_once(self):
        """The weekly ingest hands over stored events and this week's together."""
        event = nfl("g1", "Baltimore Ravens", "Buffalo Bills", 47, 3)
        [q] = build(event, self._others() + [event])
        assert "the biggest winning margin of the 2018 regular season" in q["prompt"]

    def test_a_playoff_game_never_counts_towards_a_regular_season_record(self):
        event = nfl("g1", "Baltimore Ravens", "Buffalo Bills", 40, 3)
        playoff = nfl("p1", "New Orleans Saints", "Arizona Cardinals", 60, 3,
                      reason="playoff_blowout", rnd="Divisional Round")
        [q] = build(event, [SUPER_BOWL_2018, playoff,
                            nfl("o1", "Chicago Bears", "Arizona Cardinals", 20, 3),
                            nfl("o2", "Kansas City Chiefs", "Oakland Raiders", 21, 5)])
        assert "the biggest winning margin of the 2018 regular season" in q["prompt"]


class TestPhrasing:
    def test_the_same_event_always_makes_the_same_question(self):
        event = soccer("g1", "Liverpool FC", "Norwich City FC")
        assert build(event, SEASON) == build(event, SEASON)

    def test_wrong_answers_vary_across_a_competition(self):
        """Ranked by a per-question hash, so one season does not reuse one trio."""
        clubs = [f"Club {c} FC" for c in "ABCDEFGHIJKL"]
        trios = {tuple(sorted(tpl._distractors("Liverpool FC", [], [clubs], f"g{i}")))
                 for i in range(10)}
        assert len(trios) > 1


class TestTheRevealWaitsForTheAnswer:
    def _question(self):
        [q] = build(soccer("g1", "Liverpool FC", "Norwich City FC"), SEASON)
        return q

    def test_it_is_not_served_with_the_question(self):
        """It names the answer, so it travels only once the answer is locked in."""
        q = self._question()
        blob = json.dumps(play_view.public_question(q, 0, 5))
        assert "reveal" not in blob
        assert "Liverpool FC" not in blob

    def test_it_comes_back_with_the_graded_answer(self, monkeypatch):
        from lambdas.play_answer import handler as answer_h
        q = self._question()
        session = {"questionIds": [q["questionId"], "q2"], "currentIndex": 0}
        monkeypatch.setattr("lambdas.common.plays_dynamo.get_session", lambda *a: session)
        monkeypatch.setattr("lambdas.common.plays_dynamo.record_answer",
                            lambda *a, **k: {"totalPoints": 900})
        monkeypatch.setattr("lambdas.common.plays_dynamo.mark_served", lambda *a: None)
        monkeypatch.setattr("lambdas.common.questions_dynamo.get_question",
                            lambda qid: q if qid == q["questionId"] else
                            {**q, "questionId": "q2"})

        body = {"deviceId": "dev-1", "index": 0, "answer": "Liverpool FC"}
        resp = answer_h.handler({"body": json.dumps(body), "requestContext": {}}, None)
        out = json.loads(resp["body"])
        assert out["correct"] is True
        assert out["reveal"] == q["reveal"]
        assert "reveal" not in out["question"]
