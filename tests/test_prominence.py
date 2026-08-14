"""
Cross-sport notability calibration.

The bug this fixes was invisible twice over: no template copied
`notabilityScore` onto a question, so the assembler's tiebreak read zero for
every candidate and fell through to a hash — and even once carried, the scores
were not comparable, because a routine Ligue 1 win scored 82 and Ivan Rodriguez
signing as a free agent scored 60.
"""

from lambdas.common.notability import prominence


ACCOLADES = {
    "Ivan Rodriguez": {"MVP": 1},
    "Randy Johnson": {"Cy Young": 5},
    "Dick Allen": {"MVP": 1, "Rookie of the Year": 1},
}


def event(**kw):
    base = {"sport": "mlb", "league": "American League", "reason": "star_free_agent",
            "notabilityScore": 60, "facts": {"player": "A Journeyman"}}
    base.update(kw)
    return base


def test_a_journeyman_is_unchanged():
    # Nothing is invented: an event about somebody the awards source has never
    # heard of comes out exactly as it went in.
    assert prominence.adjusted_score(event(), ACCOLADES) == 60


def test_a_decorated_players_move_outranks_any_routine_league_win():
    # The comparison that started this: a forgotten 5-0 was beating one of the
    # most decorated players who ever played.
    move = prominence.adjusted_score(
        event(facts={"player": "Randy Johnson"}), ACCOLADES)
    routine_win = prominence.adjusted_score(
        event(sport="soccer", league="French Ligue 1 2016/17",
              reason="soccer_big_win", notabilityScore=82, facts={}), ACCOLADES)
    assert move > routine_win


def test_it_does_not_outrank_a_no_hitter():
    # A tiebreak among notable events, not a second notability system.
    move = prominence.adjusted_score(
        event(facts={"player": "Randy Johnson"}), ACCOLADES)
    assert move < 92


def test_a_single_award_winner_clears_a_second_tier_result():
    # Where a top-flight comparison is arguable, this one is not: a Championship
    # 5-0 was among the top fillers on the thinnest March dates.
    signing = prominence.adjusted_score(
        event(facts={"player": "Ivan Rodriguez"}), ACCOLADES)
    second_tier = prominence.adjusted_score(
        event(sport="soccer", league="English Championship 2014/15",
              reason="soccer_big_win", notabilityScore=82, facts={}), ACCOLADES)
    assert signing > second_tier


def test_more_awards_are_worth_more_but_are_capped():
    one = prominence.adjusted_score(event(facts={"player": "Ivan Rodriguez"}), ACCOLADES)
    five = prominence.adjusted_score(event(facts={"player": "Randy Johnson"}), ACCOLADES)
    assert five > one
    # Capped, because this is a tiebreak between events already judged notable,
    # not a second notability system that can outrank a perfect game.
    assert five - 60 <= prominence.MAX_AWARD_BONUS


def test_the_pitcher_key_counts_too():
    # Detectors name the person under different keys.
    assert prominence.adjusted_score(
        event(reason="no_hitter", facts={"pitcher": "Randy Johnson"}), ACCOLADES) > 60


def test_a_second_tier_result_is_marked_down():
    top = prominence.adjusted_score(
        event(sport="soccer", league="English Premier League 2014/15",
              reason="soccer_big_win", notabilityScore=82, facts={}), ACCOLADES)
    second = prominence.adjusted_score(
        event(sport="soccer", league="English Championship 2014/15",
              reason="soccer_big_win", notabilityScore=82, facts={}), ACCOLADES)
    assert second < top


def test_a_small_nations_top_flight_is_not_a_second_tier():
    # The Eredivisie is a top flight, and guessing tier from the name rather
    # than naming the second tiers would have demoted it.
    score = prominence.adjusted_score(
        event(sport="soccer", league="Netherlands Eredivisie 2018/19",
              reason="soccer_big_win", notabilityScore=82, facts={}), ACCOLADES)
    assert score == 82


def test_a_penalty_never_drives_an_event_below_the_floor():
    # Thin dates cannot afford to lose the little they have.
    score = prominence.adjusted_score(
        event(sport="soccer", league="English Championship 2014/15",
              reason="soccer_big_win", notabilityScore=42, facts={}), ACCOLADES)
    assert score >= prominence.MIN_SCORE


def test_an_event_with_no_score_is_left_alone():
    assert prominence.adjusted_score(event(notabilityScore=None), ACCOLADES) is None


def test_apply_reports_what_it_moved():
    events = [event(facts={"player": "Ivan Rodriguez"}), event()]
    assert prominence.apply(events, ACCOLADES) == 1
    assert events[0]["notabilityScore"] > 60
    assert events[1]["notabilityScore"] == 60


def test_questions_carry_the_score_so_the_tiebreak_can_read_it():
    """
    The assembler ranks on `q["notabilityScore"]`. No template put it there, so
    every candidate scored zero and selection fell through to a hash of the id
    — the picker had no notion of how notable anything was.
    """
    from lambdas.common.templates import mlb_templates as tpl
    q = tpl._q({"gameId": "g1", "year": 1990, "sport": "mlb", "league": "AL",
                "mmdd": "06-15", "reason": "no_hitter", "notabilityScore": 92,
                "sourceName": "s", "sourceDatasetRef": "r"},
               "mc", "Who threw it?", "Somebody")
    assert q["notabilityScore"] == 92
