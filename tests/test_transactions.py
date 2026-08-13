"""
Transactions are the first source here that is not a game, so the failure modes
are new: a date the archive admits it is guessing at, a deal that was undone a
week later, and a player nobody has heard of.

The Ruth sale is used as the known-answer check. It is the most famous
transaction in the sport and its details are not in dispute, which makes it the
right thing to pin.
"""

from datetime import date

import pytest

from lambdas.common.notability import transactions as tran_nb
from lambdas.common.sources import retrosheet_transactions as rst


# ------------------------------------------------------------------ parsing

def test_a_day_precise_date_parses():
    assert rst.parse_date("19191226") == date(1919, 12, 26)


@pytest.mark.parametrize("raw", ["19190000", "19191200", "1919", "", "notadate"])
def test_an_imprecise_date_is_rejected_rather_than_guessed(raw):
    """
    The archive encodes partial knowledge in the date itself. A date-anchored
    quiz cannot place "sometime in December 1919" on a calendar, and guessing a
    day would file the event on a date it did not happen.
    """
    assert rst.parse_date(raw) is None


def test_an_impossible_date_is_rejected():
    assert rst.parse_date("19190230") is None


def test_money_is_read_from_the_info_field():
    assert rst.money_amount('"$100000"') == 100000
    assert rst.money_amount("$1,500") == 1500
    assert rst.money_amount("") is None
    assert rst.money_amount("waivers") is None


@pytest.mark.parametrize("code", ["Tr", "Pv", "Tn", "Dv"])
def test_undone_deals_are_recognised(code):
    """A trade reversed a week later is a trap, not a question."""
    assert rst.is_reversal(code)


@pytest.mark.parametrize("code", ["T", "P", "F", "D"])
def test_real_deals_are_not_treated_as_reversals(code):
    assert not rst.is_reversal(code)


# ------------------------------------------------------------- notability

RUTH = "ruthb101"
NOBODY = "nobod001"

CAREERS = {
    RUTH: {"name": "Babe Ruth", "starts": 2000, "isPitcher": False},
    NOBODY: {"name": "Fred Nobody", "starts": 12, "isPitcher": False},
    "acepi001": {"name": "Ace Pitcher", "starts": 350, "isPitcher": True},
}

TEAM_NAMES = {}


def _resolve(_names, code, _when):
    return {"BOS": "Boston Red Sox", "NYA": "New York Yankees",
            "CHN": "Chicago Cubs"}.get(code)


def _deal(tran_id, players, type_code="T", money=None, when=date(1919, 12, 26)):
    return {
        "tranId": tran_id,
        "date": when,
        "year": when.year,
        "mmdd": f"{when.month:02d}-{when.day:02d}",
        "type": type_code,
        "typeLabel": rst.TYPE_LABEL[type_code],
        "money": money,
        "legs": [{"playerId": p, "fromTeam": "BOS", "toTeam": "NYA",
                  "money": money} for p in players],
        "sourceName": rst.SOURCE_NAME,
        "sourceDatasetRef": rst.TRAN_URL,
    }


def _detect(deals):
    return tran_nb.detect(deals, CAREERS, TEAM_NAMES, _resolve)


def test_the_ruth_sale_is_notable():
    events = _detect([_deal("46087", [RUTH], "P", money=100000)])

    assert len(events) == 1
    e = events[0]
    assert e["mmdd"] == "12-26"
    assert e["year"] == 1919
    assert e["facts"]["player"] == "Babe Ruth"
    assert e["facts"]["fromTeam"] == "Boston Red Sox"
    assert e["facts"]["toTeam"] == "New York Yankees"
    assert e["facts"]["amount"] == 100000


def test_a_deal_for_a_journeyman_produces_nothing():
    """
    87,245 deals survive parsing and almost all of them are this. If the filter
    lets them through, the corpus is noise.
    """
    assert _detect([_deal("1", [NOBODY])]) == []


def test_a_pitcher_is_judged_on_a_pitcher_threshold():
    """
    Pitchers start once every five days, so the same career is a fifth of the
    starts. One threshold for both would exclude every pitcher who ever played.
    """
    events = _detect([_deal("2", ["acepi001"])])
    assert len(events) == 1
    assert events[0]["facts"]["player"] == "Ace Pitcher"


def test_an_unnameable_team_kills_the_event():
    """
    The same rule as the game logs: "traded to the CL4" must never reach a
    prompt. A missing name is a deal to skip, not a name to invent.
    """
    def unresolvable(_names, _code, _when):
        return None

    events = tran_nb.detect([_deal("3", [RUTH])], CAREERS, {}, unresolvable)
    assert events == []


def test_a_multi_player_deal_is_reported_as_one_blockbuster():
    deal = _deal("4", [RUTH, NOBODY, "acepi001", NOBODY])
    events = _detect([deal])

    assert len(events) == 1, "one deal is one event, not one per player"
    assert events[0]["reason"] == "blockbuster_trade"
    # The journeyman has no career entry to name, so he is not in the list.
    assert "Babe Ruth" in events[0]["facts"]["allPlayers"]


def test_direction_comes_from_the_headline_players_own_leg():
    """
    Regression: the real 1916 Speaker deal, verbatim.

    Retrosheet records it as four rows sharing one transaction id. Speaker went
    Boston to Cleveland; two other players and the cash went the other way.
    Reading the first non-empty code from any leg produced "Cleveland Indians
    -> Boston Red Sox" attributed to Speaker - backwards, and plausible enough
    that only checking it against a known fact catches it.

    Every synthetic deal in this file used to point one way, which is exactly
    why the bug survived a passing suite.
    """
    careers = dict(CAREERS, speat101={"name": "Tris Speaker", "starts": 2500,
                                      "isPitcher": False})

    def resolve(_names, code, _when):
        return {"BOS": "Boston Red Sox", "CLE": "Cleveland Indians"}.get(code)

    deal = {
        "tranId": "58981",
        "date": date(1916, 4, 9), "year": 1916, "mmdd": "04-09",
        "type": "T", "typeLabel": "trade", "money": 55000,
        "legs": [
            # The cash row carries no player and points Cleveland -> Boston.
            {"playerId": "", "fromTeam": "CLE", "toTeam": "BOS", "money": 55000},
            {"playerId": NOBODY, "fromTeam": "CLE", "toTeam": "BOS", "money": None},
            {"playerId": "speat101", "fromTeam": "BOS", "toTeam": "CLE",
             "money": None},
        ],
        "sourceName": rst.SOURCE_NAME,
        "sourceDatasetRef": rst.TRAN_URL,
    }

    events = tran_nb.detect([deal], careers, {}, resolve)

    assert len(events) == 1
    facts = events[0]["facts"]
    assert facts["player"] == "Tris Speaker"
    assert facts["fromTeam"] == "Boston Red Sox"
    assert facts["toTeam"] == "Cleveland Indians"


def test_cash_in_a_trade_is_not_a_sale():
    """
    A trade that included money is still a trade. Recording it as `amount`
    would produce "Tris Speaker was sold for $55,000" about a deal in which
    players moved both ways.
    """
    careers = dict(CAREERS, speat101={"name": "Tris Speaker", "starts": 2500,
                                      "isPitcher": False})
    deal = _deal("58981", ["speat101"], "T", money=55000)

    events = tran_nb.detect([deal], careers, {}, _resolve)

    assert len(events) == 1
    assert events[0]["reason"] == "star_trade"
    assert "amount" not in events[0]["facts"]
    assert events[0]["facts"]["cashIncluded"] == 55000


def test_landmark_sales_are_ranked_inside_their_own_decade():
    """
    $400,000 in 1977 and $100,000 in 1919 are not comparable. Ranking across
    the whole file would sort by inflation and call every modern deal a
    landmark.
    """
    old = _deal("5", [NOBODY], "P", money=100000, when=date(1919, 8, 1))
    new = _deal("6", [NOBODY], "P", money=400000, when=date(1977, 2, 19))

    events = _detect([old, new])
    reasons = {e["year"]: e["reason"] for e in events}

    # Neither player is a star, so both qualify only as landmark sales - one
    # from each decade, rather than the 1977 deal crowding out the 1919 one.
    assert reasons == {1919: "landmark_sale", 1977: "landmark_sale"}
