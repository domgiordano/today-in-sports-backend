"""
Lineup questions.

One mistake this format must never make: naming a decoy who actually played,
which would mark a correct answer wrong. That is what most of these cover.
"""

from lambdas.common.templates import lineup_templates as tpl


def _event(reason="no_hitter", year=1991, lineup=None):
    return {
        "sport": "mlb", "league": "MLB", "reason": reason,
        "gameId": f"g-{year}", "gameDate": f"{year}-08-13",
        "year": year, "mmdd": "08-13", "title": "t",
        # The teams are what name the game in the prompt; without them the
        # question is unanswerable and the template declines to build it.
        "facts": {"awayTeam": "Orioles", "homeTeam": "Athletics"},
        "lineups": lineup if lineup is not None
                   else [f"Player {i}" for i in range(18)],
        "sourceName": "Retrosheet", "sourceDatasetRef": "https://retrosheet.org",
    }


def _ctx(year=1991, extra=30):
    return {"namesByEra": {
        year // 10: [f"Other {i}" for i in range(extra)],
    }}


def test_a_full_lineup_makes_a_question():
    q = tpl.who_started(_event(), _ctx())[0]

    assert q["type"] == "multi"
    assert len(q["answer"]) == tpl.REAL_NAMES
    assert len(q["options"]) == tpl.REAL_NAMES + tpl.DECOY_NAMES
    assert q["chooseCount"] == tpl.REAL_NAMES


def test_every_correct_name_is_among_the_options():
    q = tpl.who_started(_event(), _ctx())[0]
    assert set(q["answer"]) <= set(q["options"])


def test_no_decoy_actually_played_in_the_game():
    """
    The one mistake this format must not make: a decoy who was really there
    marks a correct answer wrong, and the player has no way to know why.
    """
    lineup = [f"Player {i}" for i in range(18)]
    # An era pool that overlaps the real lineup, which is the realistic case.
    ctx = {"namesByEra": {199: lineup + [f"Other {i}" for i in range(20)]}}

    q = tpl.who_started(_event(lineup=lineup), ctx)[0]
    decoys = set(q["options"]) - set(q["answer"])
    assert not (decoys & set(lineup))


def test_a_partial_lineup_is_skipped():
    """
    Picking four from an incomplete list risks calling a real starter a decoy.
    """
    assert tpl.who_started(_event(lineup=["a", "b", "c"]), _ctx()) == []


def test_a_game_with_no_recorded_lineup_is_skipped():
    assert tpl.who_started(_event(lineup=[]), _ctx()) == []

    # Nineteenth-century game logs often record no lineup at all, so the key
    # can be missing rather than empty.
    without = _event()
    without.pop("lineups")
    assert tpl.who_started(without, _ctx()) == []


def test_too_few_decoys_available_produces_nothing():
    """Better no question than one with fewer options than it claims."""
    assert tpl.who_started(_event(), {"namesByEra": {199: ["Only One"]}}) == []


def test_generation_is_deterministic():
    a = tpl.who_started(_event(), _ctx())[0]
    b = tpl.who_started(_event(), _ctx())[0]
    assert a["options"] == b["options"]
    assert a["answer"] == b["answer"]
    assert a["questionId"] == b["questionId"]


def test_context_buckets_names_by_decade():
    ctx = tpl.build_context([
        _event(year=1919, lineup=["Babe Ruth"]),
        _event(year=1991, lineup=["Nolan Ryan"]),
    ])
    assert "Babe Ruth" in ctx["namesByEra"][191]
    assert "Babe Ruth" not in ctx["namesByEra"][199]


def test_only_marquee_games_get_lineup_questions():
    """A midweek blowout does not deserve eight names of anybody's attention."""
    assert tpl.generate([_event(reason="blowout")], _ctx()) == []
    assert tpl.generate([_event(reason="no_hitter")], _ctx())


def test_generated_questions_validate():
    for q in tpl.generate([_event()], _ctx()):
        assert tpl.validate(q) == []


def test_validation_catches_an_answer_outside_the_options():
    q = tpl.who_started(_event(), _ctx())[0]
    dropped = q["answer"][0]
    q["options"] = [o for o in q["options"] if o != dropped] + ["Someone Else"]
    assert "a correct name is missing from the options" in tpl.validate(q)


def test_a_game_with_no_teams_makes_no_question():
    """
    The prompt has to name the fixture. Without it a player is asked to
    identify a game they were never told about, which cannot be reasoned
    toward — only recognised. No question beats an unanswerable one.
    """
    event = _event()
    event["facts"] = {}
    assert tpl.who_started(event, _ctx()) == []


def test_the_prompt_names_both_teams():
    q = tpl.who_started(_event(), _ctx())[0]
    assert "Orioles" in q["prompt"] and "Athletics" in q["prompt"]
    assert "this game" not in q["prompt"]
