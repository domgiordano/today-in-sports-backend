"""
Historically correct team names, and games that are not league games.

Both faults showed up the same way: 459 questions flagged "team name may be
anachronistic", which was the review queue noticing that the corpus could not
tell 1962 from today.
"""

from lambdas.common.sources import nhl
from lambdas.common.notability import nba as nba_nb
from lambdas.common.notability import nhl as nhl_nb


TEAMS = {
    "MNS": "Minnesota North Stars",
    "DAL": "Dallas Stars",
    "CGS": "California Golden Seals",
    "AFM": "Atlanta Flames",
    "ATL": "Atlanta Thrashers",
    "WIN": "Winnipeg Jets",
    "WPG": "Winnipeg Jets",
    "MTL": "Montréal Canadiens",
}


# ------------------------------------------------------------- nhl team names

def test_a_defunct_club_resolves_to_its_own_name():
    # The franchise endpoint keyed every franchise on its *current* code, so
    # franchise 15 was "DAL" and Minnesota's 2,235 games printed the bare
    # string "MNS" as a team name.
    assert nhl.team_name(TEAMS, "MNS") == "Minnesota North Stars"
    assert nhl.team_name(TEAMS, "CGS") == "California Golden Seals"


def test_the_same_franchise_reads_differently_in_each_era():
    # One franchise, two identities, and the tricode in the game log already
    # says which era it was - so no season window can pick the wrong one.
    assert nhl.team_name(TEAMS, "MNS") != nhl.team_name(TEAMS, "DAL")


def test_a_code_that_is_not_a_club_resolves_to_nothing():
    # None, not the raw code: the caller has to be able to tell a club from a
    # national side, and a fallback string hides the difference.
    assert nhl.team_name(TEAMS, "URS") is None
    assert nhl.team_name(TEAMS, "ALL") is None


def _stubbed(module, payload, monkeypatch=None):
    """Run load_teams against a fixed payload instead of the network."""
    module._get = lambda url: payload
    return None


def test_disambiguating_years_are_stripped_from_names():
    # The API needs "Winnipeg Jets (1979)" to keep two rows apart. A quiz
    # prompt does not.
    data = {"data": [
        {"triCode": "WIN", "fullName": "Winnipeg Jets (1979)"},
        {"triCode": "OTT", "fullName": "Ottawa Senators (1917)"},
        {"triCode": "BOS", "fullName": "Boston Bruins"},
    ]}
    parsed = nhl.load_teams(_stubbed(nhl, data))
    assert parsed["WIN"] == "Winnipeg Jets"
    assert parsed["OTT"] == "Ottawa Senators"
    assert parsed["BOS"] == "Boston Bruins"


# --------------------------------------------------------- non-league fixtures

def game(away="MTL", home="BOS", league=True, score=(3, 2)):
    return {
        "isLeagueGame": league,
        "gameDate": "1972-09-28",
        "away": {"team": TEAMS.get(away, away), "teamId": away,
                 "score": score[0], "isWinner": score[0] > score[1]},
        "home": {"team": TEAMS.get(home, home), "teamId": home,
                 "score": score[1], "isWinner": score[1] > score[0]},
    }


def test_a_summit_series_game_is_not_an_nhl_game():
    assert not nhl_nb.has_usable_teams(game(away="URS", league=False))


def test_a_league_game_still_counts():
    assert nhl_nb.has_usable_teams(game())


def test_a_corpus_predating_the_field_still_loads():
    # `isLeagueGame` is absent on anything normalised before it existed, and
    # treating absent as "not a league game" would silently empty the corpus.
    old = game()
    del old["isLeagueGame"]
    assert nhl_nb.has_usable_teams(old)


# ------------------------------------------------------------ nba zero scores

def nba_game(away_score, home_score):
    return {
        "gameDate": "1946-11-02",
        "away": {"team": "New York Knicks", "score": away_score},
        "home": {"team": "Chicago Stags", "score": home_score},
    }


def test_a_game_with_no_recorded_score_is_not_an_event():
    """
    The source has no scores for much of the 1940s and returns 0 rather than
    null, which the low-score detector read as the lowest total in history:
    1,415 of 1,898 NBA events were "combined for only 0 points".
    """
    assert not nba_nb.has_credible_score(nba_game(0, 0))
    assert not nba_nb.has_credible_score(nba_game(106, 0))


def test_a_real_low_scoring_game_still_counts():
    # 19-18 really happened in 1950, but that is Retrosheet-grade rare; the
    # floor sits below any credible NBA total and above a missing one.
    assert nba_nb.has_credible_score(nba_game(78, 65))


def test_a_missing_score_is_not_a_zero():
    assert not nba_nb.has_credible_score(nba_game(None, 88))


# ------------------------------------------------- the cutoff has to cover it

def test_a_post_1980_basketball_relocation_is_still_held():
    """
    The cutoff was 1980, on the assumption that relocations were a mid-century
    thing. Seattle became Oklahoma City in 2008 and New Jersey became Brooklyn
    in 2012, so 185 events named a club under a name it did not have yet — and
    every one was after 1980, sailing straight past the flag.
    """
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "auto_review.py"
    spec = importlib.util.spec_from_file_location("auto_review", path)
    ar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ar)

    q = {"type": "numeric", "sport": "nba", "year": 1988,
         "prompt": "On December 2, 1988, the Oklahoma City Thunder routed the "
                   "LA Clippers 154-104. What was the margin?",
         "numericAnswer": 50, "answer": 50}
    assert any("did not carry that name" in f for f in ar.flags_for(q))


def test_a_registered_two_letter_club_name_is_not_a_stray_code():
    """
    "LA Clippers" is the club's actual registered name since 2015. The stray
    -code guard read the "LA" as an unresolved Retrosheet id and threw away
    nine real questions.
    """
    from lambdas.common.templates import mlb_templates as tpl
    q = {"type": "numeric", "tier": 1, "sourceName": "s", "sourceDatasetRef": "r",
         "numericAnswer": 34, "answer": 34,
         "prompt": "On April 21, 2014, the LA Clippers routed the Golden State "
                   "Warriors 138-98. What was the margin?"}
    assert "unresolved team code in prompt" not in tpl.validate(q)


def test_a_genuine_stray_code_is_still_caught():
    from lambdas.common.templates import mlb_templates as tpl
    q = {"type": "numeric", "tier": 1, "sourceName": "s", "sourceDatasetRef": "r",
         "numericAnswer": 3, "answer": 3,
         "prompt": "On May 2, 1886, the CL4 beat the Pittsburgh Alleghenys. "
                   "How many runs did they score?"}
    assert "unresolved team code in prompt" in tpl.validate(q)


# ------------------------------------------- basketball, where the name is not known

def nba_event(year, reason="nba_blowout", **facts):
    base = {"winningTeam": "Sacramento Kings", "losingTeam": "Atlanta Hawks",
            "winningScore": 120, "losingScore": 61, "margin": 59,
            "combinedPoints": 181}
    base.update(facts)
    return {
        "sport": "nba", "league": "NBA", "reason": reason,
        "gameId": f"g{year}", "gameDate": f"{year}-01-20", "mmdd": "01-20",
        "year": year, "facts": base,
        "sourceName": "balldontlie", "sourceDatasetRef": "r",
    }


# What each franchise was called in 1953, as the infoboxes give it.
NBA_1953 = {"Sacramento Kings": [["Rochester Royals", 1945, 1957]],
            "Atlanta Hawks": [["Milwaukee Hawks", 1951, 1955]]}


def test_an_old_basketball_question_names_the_clubs_as_they_were_called():
    """
    balldontlie returns the modern franchise for a 1953 game, so naming the
    clubs used to assert two cities neither had reached and basketball went
    unnamed before 2015. The franchise histories resolve it instead.
    """
    from lambdas.common.templates import winter_templates as tpl
    [q] = tpl.nba_blowout_margin(nba_event(1953), {"nba_franchises": NBA_1953})
    assert "Rochester Royals" in q["prompt"]
    assert "Milwaukee Hawks" in q["prompt"]
    assert "Sacramento" not in q["prompt"]
    assert "Atlanta" not in q["prompt"]
    assert q["numericAnswer"] == 59


def test_a_club_the_source_cannot_place_is_still_left_unnamed():
    """
    balldontlie attaches the modern Denver Nuggets to games from 1949, eighteen
    years before that franchise existed. Resolving to nothing must still mean
    naming nothing.
    """
    from lambdas.common.templates import winter_templates as tpl
    ctx = {"nba_franchises": {"Denver Nuggets": [["Denver Nuggets", 1967, 9999]],
                              "Atlanta Hawks": [["Milwaukee Hawks", 1951, 1955]]}}
    [q] = tpl.nba_blowout_margin(nba_event(1949, winningTeam="Denver Nuggets"),
                                 ctx)
    assert "Nuggets" not in q["prompt"]
    assert "an NBA game" in q["prompt"]


def test_a_modern_basketball_question_still_names_them():
    from lambdas.common.templates import winter_templates as tpl
    modern = {"Sacramento Kings": [["Sacramento Kings", 1985, 9999]],
              "Atlanta Hawks": [["Atlanta Hawks", 1968, 9999]]}
    [q] = tpl.nba_blowout_margin(nba_event(2022), {"nba_franchises": modern})
    assert "Sacramento Kings" in q["prompt"]


def test_an_old_combined_points_question_names_them_correctly():
    from lambdas.common.templates import winter_templates as tpl
    [q] = tpl.nba_combined_points(nba_event(1953, reason="nba_low_score"),
                                  {"nba_franchises": NBA_1953})
    assert "Rochester Royals" in q["prompt"]
    assert "Sacramento" not in q["prompt"]
    assert "low-scoring" in q["prompt"]


def test_an_old_who_won_question_uses_era_names_for_answer_and_distractors():
    """
    An era-correct answer against modern distractors is solvable without
    knowing anything: three clubs that did not exist under those names in 1970
    single out the fourth.
    """
    from lambdas.common.templates import winter_templates as tpl
    ctx = {
        "nba_teams": ["Sacramento Kings", "Atlanta Hawks", "Brooklyn Nets",
                      "Oklahoma City Thunder", "LA Clippers"],
        "nba_franchises": {
            "Sacramento Kings": [["Cincinnati Royals", 1957, 1972]],
            "Atlanta Hawks": [["Atlanta Hawks", 1968, 9999]],
            "Brooklyn Nets": [["New York Nets", 1968, 1977]],
            "Oklahoma City Thunder": [["Seattle SuperSonics", 1967, 2008]],
            "LA Clippers": [["Buffalo Braves", 1970, 1978]],
        },
    }
    [q] = tpl.nba_late_playoff_winner(
        nba_event(1970, reason="nba_late_playoff"), ctx)
    assert q["answer"] == "Cincinnati Royals"
    assert "Sacramento" not in str(q["distractors"])
    assert "Seattle SuperSonics" in q["distractors"]


def test_a_question_naming_no_club_is_not_flagged():
    """
    The flag exists to catch a wrong city. A question that names no club
    cannot have one, and flagging by sport and year alone would hold back the
    very questions the templates rewrote to be safe.
    """
    import importlib.util, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "auto_review.py"
    spec = importlib.util.spec_from_file_location("auto_review", path)
    ar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ar)

    safe = {"type": "numeric", "sport": "nba", "year": 1953,
            "prompt": "Two NBA teams met on January 20, 1953 in a famously "
                      "low-scoring game. How many points did they score "
                      "between them?",
            "numericAnswer": 121, "answer": 121}
    assert ar.flags_for(safe) == []

    named = dict(safe, prompt="The Sacramento Kings and Atlanta Hawks met on "
                              "January 20, 1953. How many points did they score?")
    assert any("did not carry that name" in f for f in ar.flags_for(named))


def test_basketball_is_no_longer_held_back_by_the_relocation_flag():
    """
    The flag existed because balldontlie could not say what a club was called
    in 1953. The franchise histories can, so a correctly-named old question
    must now pass review rather than sit in a queue forever.
    """
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "scripts" / "auto_review.py")
    spec = importlib.util.spec_from_file_location("auto_review", path)
    ar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ar)

    q = {"type": "numeric", "sport": "nba", "year": 1953,
         "prompt": "The Rochester Royals and Milwaukee Hawks met on January 20, "
                   "1953 in a famously low-scoring game. How many points did "
                   "the two teams score between them?",
         "numericAnswer": 118, "answer": 118}
    assert ar.flags_for(q) == []


def test_a_name_reused_by_a_later_franchise_is_not_an_error():
    """
    The original Baltimore Bullets folded in 1954; the club that became the
    Wizards took the name in 1963. Checking historical names against the
    second one's dates flagged twenty-two correct questions about the first.
    """
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "scripts" / "auto_review.py")
    spec = importlib.util.spec_from_file_location("auto_review", path)
    ar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ar)

    index = {"Washington Wizards": [["Baltimore Bullets", 1963, 1973],
                                    ["Washington Wizards", 1997, 9999]]}
    old = {"sport": "nba", "year": 1947,
           "prompt": "The Baltimore Bullets met the Chicago Stags."}
    assert ar._anachronistic_clubs(old, index) == []

    # The modern name in an old question is still caught.
    leaked = {"sport": "nba", "year": 1947,
              "prompt": "The Washington Wizards met the Chicago Stags."}
    assert ar._anachronistic_clubs(leaked, index) == ["Washington Wizards"]


def test_a_relocation_does_not_flag_its_own_first_winter():
    """An NBA season spans two calendar years."""
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "scripts" / "auto_review.py")
    spec = importlib.util.spec_from_file_location("auto_review", path)
    ar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ar)

    index = {"Sacramento Kings": [["Sacramento Kings", 1985, 9999]]}
    # Season 1984 runs into calendar 1985; season 1985 into 1986.
    assert ar._anachronistic_clubs(
        {"sport": "nba", "year": 1984,
         "prompt": "The Sacramento Kings won."}, index) == []
    assert ar._anachronistic_clubs(
        {"sport": "nba", "year": 1980,
         "prompt": "The Sacramento Kings won."}, index) == ["Sacramento Kings"]
