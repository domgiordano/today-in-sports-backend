"""
Transaction questions.

The failure mode specific to this template set is the anachronistic distractor:
offering a franchise that did not exist yet hands the answer to anyone who
knows when the club was founded. That is the main thing pinned here.
"""

from lambdas.common.templates import transaction_templates as tpl


def _event(reason="star_trade", year=1971, **facts):
    base = {
        "player": "Frank Robinson",
        "fromTeam": "Baltimore Orioles",
        "toTeam": "Los Angeles Dodgers",
        "transactionType": "trade",
        "playerCount": 1,
        "allPlayers": ["Frank Robinson"],
    }
    base.update(facts)
    return {
        "sport": "mlb", "league": "MLB", "reason": reason,
        "gameId": f"tran-{year}-{reason}",
        "gameDate": f"{year}-12-02", "year": year, "mmdd": "12-02",
        "title": "t", "facts": base,
        "sourceName": "Retrosheet transaction database",
        "sourceDatasetRef": "https://www.retrosheet.org/transactions/tranDB.zip",
    }


CTX = {
    "teamsByEra": {
        191: ["Boston Red Sox", "New York Yankees", "Chicago White Sox",
              "Detroit Tigers", "Cleveland Indians"],
        197: ["Baltimore Orioles", "Los Angeles Dodgers", "New York Mets",
              "Oakland Athletics", "Cincinnati Reds"],
    },
    "allTeams": ["Arizona Diamondbacks", "Boston Red Sox", "New York Yankees"],
    "starNames": ["Hank Aaron", "Willie Mays", "Bob Gibson", "Frank Robinson"],
}


def test_a_destination_question_names_the_receiving_club():
    qs = tpl.mc_destination(_event(), CTX)
    assert len(qs) == 1
    assert qs[0]["answer"] == "Los Angeles Dodgers"
    assert "Baltimore Orioles" in qs[0]["prompt"]
    assert len(qs[0]["distractors"]) == 3


def test_distractors_are_clubs_that_existed_at_the_time():
    """
    A 1919 question offering the Arizona Diamondbacks is free to anyone who
    knows the franchise is from 1998. Wrong answers have to be contemporaries.
    """
    qs = tpl.mc_destination(
        _event(year=1919, fromTeam="Boston Red Sox",
               toTeam="New York Yankees", player="Babe Ruth"), CTX)

    assert len(qs) == 1
    assert set(qs[0]["distractors"]) <= set(CTX["teamsByEra"][191])
    assert "Arizona Diamondbacks" not in qs[0]["distractors"]


def test_the_real_answer_is_never_also_a_distractor():
    for q in tpl.generate([_event()], CTX):
        if q["type"] == "mc":
            assert q["answer"] not in q["distractors"]


def test_a_deal_with_no_named_destination_produces_nothing():
    assert tpl.mc_destination(_event(toTeam=None), CTX) == []


def test_who_moved_uses_other_stars_as_distractors():
    qs = tpl.mc_who_moved(_event(), CTX)
    assert len(qs) == 1
    assert qs[0]["answer"] == "Frank Robinson"
    assert "Frank Robinson" not in qs[0]["distractors"]


def test_sale_price_tolerance_scales_with_the_amount():
    """
    Sale prices span three orders of magnitude. A flat tolerance would make the
    $1,500 deals unanswerable and the $400,000 ones free.
    """
    cheap = tpl.numeric_sale_price(
        _event(reason="landmark_sale", amount=1500), CTX)[0]
    dear = tpl.numeric_sale_price(
        _event(reason="landmark_sale", amount=400000), CTX)[0]

    assert cheap["tolerance"] < dear["tolerance"]
    assert cheap["numericAnswer"] == 1500
    assert dear["numericAnswer"] == 400000


def test_a_trade_with_cash_produces_no_sale_price_question():
    """`cashIncluded` is deliberately not `amount` - see the notability tests."""
    assert tpl.numeric_sale_price(_event(cashIncluded=55000), CTX) == []


def test_deal_size_only_fires_for_genuine_blockbusters():
    small = _event(reason="blockbuster_trade", playerCount=2)
    assert tpl.numeric_deal_size(small, CTX) == []

    big = _event(reason="blockbuster_trade", playerCount=6)
    qs = tpl.numeric_deal_size(big, CTX)
    assert len(qs) == 1 and qs[0]["numericAnswer"] == 6


def test_generated_questions_all_pass_validation():
    events = [_event(r, playerCount=5, amount=100000)
              for r in tpl.TEMPLATES]
    questions = tpl.generate(events, CTX)

    assert questions, "the template set produced nothing at all"
    for q in questions:
        assert tpl.validate(q) == [], q["prompt"]
        assert q["sourceDatasetRef"]
        assert q["status"] == "draft"


def test_context_buckets_teams_by_decade():
    ctx = tpl.build_context([
        _event(year=1919, fromTeam="Boston Red Sox", toTeam="New York Yankees"),
        _event(year=1971),
    ])
    assert "Boston Red Sox" in ctx["teamsByEra"][191]
    assert "Boston Red Sox" not in ctx["teamsByEra"][197]


def test_who_moved_distractors_are_contemporaries():
    """
    A 1996 question offering Wally Moses (1930s) and Dan Uggla (debuted 2006)
    is solvable by elimination without knowing anything about the trade. Found
    by playing a real quiz, not by a test.
    """
    ctx = dict(CTX, namesByEra={
        199: ["Barry Bonds", "Ken Griffey", "Greg Maddux", "Frank Thomas"],
        193: ["Wally Moses", "Jimmie Foxx"],
        200: ["Dan Uggla", "Albert Pujols"],
    })

    qs = tpl.mc_who_moved(_event(year=1996), ctx)
    assert len(qs) == 1
    assert set(qs[0]["distractors"]) <= set(ctx["namesByEra"][199])


def test_a_thin_era_widens_rather_than_dropping_the_question():
    ctx = dict(CTX, namesByEra={
        199: ["Barry Bonds"],
        198: ["Rickey Henderson", "Wade Boggs"],
        200: ["Albert Pujols", "Derek Jeter"],
    })
    qs = tpl.mc_who_moved(_event(year=1996), ctx)
    assert len(qs) == 1, "an era with few names should widen, not give up"


def test_context_buckets_players_by_decade():
    ctx = tpl.build_context([
        _event(year=1919, player="Babe Ruth"),
        _event(year=1996, player="Gary Sheffield"),
    ])
    assert "Babe Ruth" in ctx["namesByEra"][191]
    assert "Babe Ruth" not in ctx["namesByEra"][199]
