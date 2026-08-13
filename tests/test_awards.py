"""
Awards.

This nearly became a hand-curated file on the assumption that award results
were not available as structured data. They are, back to 1931, with dates - so
the tests here are about shape and counting rather than about data entry.
"""

from lambdas.common.sources import mlb_awards as aw
from lambdas.common.templates import award_templates as tpl


def _row(player, award_id="ALCY", season=1999):
    return {
        "awardId": award_id,
        "awardName": aw.AWARDS[award_id]["label"],
        "awardShort": aw.AWARDS[award_id]["short"],
        "family": aw.FAMILY[award_id],
        "season": season,
        "player": player,
        "date": f"{season}-11-01",
        "sourceName": aw.SOURCE_NAME,
        "sourceDatasetRef": "https://statsapi.mlb.com/",
    }


# ---------------------------------------------------------------- counting

def test_a_career_total_counts_every_year():
    rows = [_row("Randy Johnson", "NLCY", y) for y in (1999, 2000, 2001, 2002)]
    assert aw.accolade_index(rows)["Randy Johnson"] == {"Cy Young": 4}


def test_the_two_leagues_count_as_one_award():
    """A three-time MVP is a three-time MVP whichever league he was in."""
    rows = [_row("Frank Robinson", "NLMVP", 1961),
            _row("Frank Robinson", "ALMVP", 1966)]
    assert aw.accolade_index(rows)["Frank Robinson"] == {"MVP": 2}


def test_different_awards_are_counted_separately():
    rows = [_row("Someone", "ALMVP", 1999), _row("Someone", "ALCY", 1999)]
    assert aw.accolade_index(rows)["Someone"] == {"MVP": 1, "Cy Young": 1}


# ---------------------------------------------------------------- phrasing

def test_a_repeat_winner_reads_as_a_clue():
    """"three-time Cy Young winner" is a clue; "Cy Young: 3" is a table row."""
    assert aw.describe_accolades({"Cy Young": 3}) == \
        "He was a three-time Cy Young winner."


def test_a_single_win_is_phrased_differently():
    assert "at least once" in aw.describe_accolades({"MVP": 1})


def test_the_most_impressive_honour_leads():
    phrase = aw.describe_accolades({"MVP": 1, "Cy Young": 4})
    assert "Cy Young" in phrase and "four-time" in phrase


def test_no_honours_produces_no_phrase():
    assert aw.describe_accolades({}) is None
    assert aw.describe_accolades(None) is None


# --------------------------------------------------------------- questions

def _event(player="Barry Bonds", award="NL MVP", year=2001):
    return {
        "sport": "mlb", "league": "MLB", "reason": "award_winner",
        "gameId": f"award-{year}", "gameDate": f"{year}-11-01",
        "year": year, "mmdd": "11-01", "title": "t",
        "facts": {"player": player, "award": award,
                  "awardFull": "National League Most Valuable Player",
                  "season": year},
        "sourceName": "MLB Stats API",
        "sourceDatasetRef": "https://statsapi.mlb.com/",
    }


CTX = {"winnersByAward": {"NL MVP": [
    "Barry Bonds", "Chipper Jones", "Jeff Kent", "Sammy Sosa", "Ryan Howard"]}}


def test_distractors_are_other_winners_of_the_same_award():
    """
    Otherwise the question is "which of these is a famous baseball player",
    which is a different and much easier question.
    """
    q = tpl.mc_who_won(_event(), CTX)[0]
    assert q["answer"] == "Barry Bonds"
    assert set(q["distractors"]) <= set(CTX["winnersByAward"]["NL MVP"])
    assert "Barry Bonds" not in q["distractors"]


def test_too_few_other_winners_produces_no_question():
    thin = {"winnersByAward": {"NL MVP": ["Barry Bonds", "One Other"]}}
    assert tpl.mc_who_won(_event(), thin) == []


def test_the_season_question_allows_a_near_miss():
    q = tpl.numeric_award_year(_event(), CTX)[0]
    assert q["numericAnswer"] == 2001
    assert q["tolerance"] >= 1, "knowing the era should be worth something"


def test_generated_questions_validate():
    questions = tpl.generate([_event()], CTX)
    assert questions
    for q in questions:
        assert tpl.validate(q) == []


def test_an_abbreviated_award_does_not_trip_the_team_code_guard():
    """
    "the NL MVP" looks exactly like "the CL4" to the raw-team-code guard, which
    cannot tell a league abbreviation from a Retrosheet id. The full name is
    used instead, and it reads better.
    """
    q = tpl.mc_who_won(_event(), CTX)[0]
    assert tpl.validate(q) == []
    assert "National League Most Valuable Player" in q["prompt"]


def test_context_is_built_from_the_awards_themselves():
    ctx = tpl.build_context([_event("Barry Bonds"), _event("Jeff Kent", year=2000)])
    assert set(ctx["winnersByAward"]["NL MVP"]) == {"Barry Bonds", "Jeff Kent"}


def test_a_year_is_not_mistaken_for_a_team_code():
    """
    Regression in the shared validator. Retrosheet ids always start with a
    letter - CL4, NYA, BOS - but the guard's character class also matched
    digits, so "the 2001 season" was rejected as an unresolved team code.
    """
    from lambdas.common.templates.mlb_templates import validate

    q = {"type": "numeric", "tier": 3, "answer": 5, "numericAnswer": 5,
         "prompt": "How many games did they win in the 2001 season?",
         "sourceDatasetRef": "x", "sourceName": "y"}
    assert validate(q) == []


def test_a_real_team_code_is_still_caught():
    from lambdas.common.templates.mlb_templates import validate

    q = {"type": "numeric", "tier": 3, "answer": 5, "numericAnswer": 5,
         "prompt": "How many runs did the CL4 score that afternoon?",
         "sourceDatasetRef": "x", "sourceName": "y"}
    assert "unresolved team code in prompt" in validate(q)
