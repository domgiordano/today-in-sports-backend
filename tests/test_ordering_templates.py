"""
Ordering and clue-ladder question construction.

The failure specific to these formats is a question that gives away its own
answer: a draggable item carrying the year you are asked to sort by, or a clue
naming the person. Both are pinned here.
"""

from lambdas.common.templates import ordering_templates as tpl


def _event(year, title=None, mmdd="08-13", reason="star_trade", **facts):
    base = {"player": "Frank Robinson", "fromTeam": "Baltimore Orioles",
            "toTeam": "Los Angeles Dodgers"}
    base.update(facts)
    return {
        "sport": "mlb", "league": "MLB", "reason": reason,
        "gameId": f"e-{year}-{reason}",
        "gameDate": f"{year}-08-13", "year": year, "mmdd": mmdd,
        "title": title if title is not None else f"Something notable {reason}",
        "facts": base,
        "sourceName": "Retrosheet",
        "sourceDatasetRef": "https://retrosheet.org",
    }


def _years(*years):
    return [_event(y, title=f"Event number {y % 100}") for y in years]


# ------------------------------------------------------------------ ordering

def test_four_distinct_years_make_an_ordering_question():
    qs = tpl.chronological(_years(1918, 1954, 1986, 2016))
    assert len(qs) == 1

    q = qs[0]
    assert q["type"] == "ordering"
    assert len(q["answer"]) == 4
    assert sorted(q["items"]) == sorted(q["answer"])


def test_the_answer_is_in_chronological_order():
    qs = tpl.chronological(_years(2016, 1918, 1986, 1954))
    labels = qs[0]["answer"]
    years = [s["year"] for s in qs[0]["itemSources"]]
    assert years == sorted(years)
    assert len(labels) == 4


def test_an_item_never_carries_the_year_it_is_sorted_by():
    """
    The year is the answer. Several corpus titles lead with the date, which is
    why these questions cannot simply reuse them.
    """
    events = [
        _event(1918, title="The 1918 World Series ended"),
        _event(1954, title="Event number 54"),
        _event(1986, title="Event number 86"),
        _event(2016, title="Event number 16"),
    ]
    assert tpl.chronological(events) == []


def test_too_few_distinct_years_produces_nothing():
    assert tpl.chronological(_years(1918, 1954, 1986)) == []


def test_events_from_one_year_cannot_be_ordered():
    same = [_event(1986, title=f"Event {i}") for i in range(6)]
    for i, e in enumerate(same):
        e["gameId"] = f"g{i}"
    assert tpl.chronological(same) == []


def test_the_span_is_spread_rather_than_four_adjacent_years():
    """1974/75/76/77 is a memory test; 1918/1954/1986/2016 is a question."""
    events = _years(*range(1970, 2020))
    years = [s["year"] for s in tpl.chronological(events)[0]["itemSources"]]
    assert max(years) - min(years) > 20


def test_generation_is_deterministic():
    a = tpl.chronological(_years(1918, 1954, 1986, 2016))[0]
    b = tpl.chronological(_years(2016, 1986, 1954, 1918))[0]
    assert a["items"] == b["items"]
    assert a["questionId"] == b["questionId"]


# --------------------------------------------------------------- clue ladder

def test_a_clue_ladder_is_built_from_corpus_fields():
    qs = tpl.clue_ladder(_event(1971))
    assert len(qs) == 1

    q = qs[0]
    assert q["type"] == "clue"
    assert q["answer"] == "Frank Robinson"
    assert len(q["clues"]) >= tpl.MIN_CLUES
    assert q["clueCount"] == len(q["clues"])


def test_clues_never_contain_the_answer():
    q = tpl.clue_ladder(_event(1971))[0]
    for clue in q["clues"]:
        assert "frank robinson" not in clue.lower()


def test_an_event_with_no_person_produces_no_ladder():
    """A ladder ending in "which team" is a worse multiple-choice question."""
    e = _event(1971)
    e["facts"].pop("player")
    assert tpl.clue_ladder(e) == []


def test_the_most_revealing_clue_comes_last():
    """
    There are more clue builders than rungs, so a plain truncation dropped the
    exact date off the end and left the ladder finishing on something vaguer
    than the rung before it. Trimming happens in the middle.
    """
    q = tpl.clue_ladder(_event(1971))[0]
    assert "August" in q["clues"][-1]
    assert len(q["clues"]) <= tpl.MAX_CLUES


# ------------------------------------------------------------------ validate

def test_generated_questions_pass_validation():
    events = _years(1918, 1954, 1986, 2016)
    questions = tpl.generate(events)

    assert questions
    assert any(q["type"] == "ordering" for q in questions)
    assert any(q["type"] == "clue" for q in questions)
    for q in questions:
        assert tpl.validate(q) == [], q["prompt"]


def test_validation_rejects_items_that_do_not_match_the_answer():
    q = {"type": "ordering", "tier": 3, "answer": ["a", "b", "c", "d"],
         "items": ["a", "b", "c", "z"],
         "sourceDatasetRef": "x", "sourceName": "y"}
    assert "items are not a permutation of the answer" in tpl.validate(q)


def test_validation_rejects_a_short_ladder():
    q = {"type": "clue", "tier": 3, "answer": "x", "clues": ["one"],
         "clueCount": 1, "sourceDatasetRef": "x", "sourceName": "y"}
    assert any("clues" in p for p in tpl.validate(q))


def test_the_first_clue_anchors_the_question_to_this_date():
    """
    The ladder opened with "This happened in the 1900s", which anchors nothing
    - the answer could be any player in the history of the sport, and the
    question did not feel like it belonged to the day it was asked on.
    """
    q = tpl.clue_ladder(_event(1971))[0]
    assert q["clues"][0].startswith("On this date")


def test_a_ladder_of_only_era_and_date_is_rejected():
    """
    Regression. The first version produced 5,686 questions reading "This
    happened in the 1900s / The sport was baseball / It happened on August 15,
    1903 - who is this?", which is not answerable by anyone.
    """
    e = _event(1903, reason="no_hitter")
    e["facts"] = {"player": "Noodles Hahn"}
    assert tpl.clue_ladder(e) == []


def test_a_ladder_says_what_the_person_actually_did():
    e = _event(1930, reason="pitcher_win_milestone")
    e["facts"] = {"player": "Earl Whitehill", "careerWins": 100,
                  "team": "Detroit Tigers"}

    q = tpl.clue_ladder(e)[0]
    joined = " ".join(q["clues"])
    assert "100th game of his career" in joined
    # And exactly once: stating the milestone and then restating it as a career
    # total spends two rungs on one fact.
    assert "won 100 games" not in joined
    assert "Detroit Tigers" in joined, "the club is the most useful clue"


def test_a_career_ladder_carries_its_numbers():
    e = _event(1892, reason="player_finale")
    e["facts"] = {"player": "Pud Galvin", "careerStarts": 686, "spanYears": 17}

    clues = tpl.clue_ladder(e)[0]["clues"]
    joined = " ".join(clues)
    assert "career ended" in joined
    assert "686 starts" in joined and "17 seasons" in joined


def test_every_ladder_has_at_least_two_identifying_clues():
    events = [
        _event(1971, reason="star_trade"),
        _event(1971, reason="star_free_agent"),
        _event(1971, reason="blockbuster_trade", playerCount=7),
    ]
    for e in events:
        for q in tpl.clue_ladder(e):
            identifying = [c for c in q["clues"]
                           if not c.startswith(tpl.GENERIC_PREFIXES)]
            assert len(identifying) >= 2, q["clues"]


def test_a_date_mixing_integer_and_string_game_ids_does_not_crash():
    """
    Game ids are not one type across sources: Retrosheet and f1db give them as
    text, the NBA and NHL feeds as integers. The tie-break that picks one event
    per year compared them raw, so the first calendar date holding both raised
    TypeError and took the whole corpus load down with it.
    """
    from lambdas.common.templates import ordering_templates as tpl

    def ev(game_id, year, sport):
        return {
            "gameId": game_id, "year": year, "mmdd": "06-15",
            "gameDate": f"{year}-06-15", "sport": sport, "league": "L",
            "reason": "x", "title": f"Something happened in {sport}",
            "sourceName": "s", "sourceDatasetRef": "r", "facts": {},
        }

    events = [
        ev("BRO193806150", 1938, "mlb"),
        ev(20120613, 2012, "nhl"),
        ev("f1-1994-06", 1994, "f1"),
        ev(41700307, 2017, "nba"),
        ev(19850615, 1985, "nhl"),
    ]

    # The assertion is that this returns rather than raising; the question it
    # produces is checked by the other tests here.
    out = tpl.chronological(events)
    assert isinstance(out, list)


def test_four_moments_prefer_four_different_kinds_of_moment():
    """
    Every event of one reason shares a sentence pattern, so four debuts read as
    the same sentence with the nouns swapped - which is what the "two items
    read almost identically" flag was catching. Choosing on reason varies the
    wording for free.
    """
    NAMES = {1950: "Al Rosen", 1960: "Ron Santo", 1970: "Thurman Munson",
             1980: "Kirk Gibson", 1990: "Jeff Bagwell", 2000: "Barry Zito",
             2010: "Buster Posey", 2020: "Kyle Lewis"}

    # Two debuts sit in the first band; later bands offer alternatives.
    events = [_event(y, title=f"{NAMES[y]} did something notable", reason=r)
              for y, r in [(1950, "player_debut"), (1960, "player_debut"),
                           (1970, "no_hitter"), (1980, "player_debut"),
                           (1990, "star_trade"), (2000, "player_finale"),
                           (2010, "player_debut"), (2020, "no_hitter")]]

    out = tpl.chronological(events)
    assert out, "a question should still be produced"

    years = [s["year"] for s in out[0]["itemSources"]]
    reasons = [e["reason"] for e in events if e["year"] in years]
    assert len(set(reasons)) > 1, "all four items came from one reason"
    # Chronological spread must survive the preference.
    assert years == sorted(years)
