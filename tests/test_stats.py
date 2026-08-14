"""
Precomputed statistics.

The summarise step is the part with judgement in it, so that is what is pinned
here: which rounds count, and what an average is an average of.
"""

from lambdas.common import stats_dynamo as stats


def _session(points=500, correct=4, seconds=8.0, identity="u1",
             quiz_date="2026-08-13", completed=True):
    return {
        "identity": identity,
        "quizDate": quiz_date,
        "totalPoints": points,
        "correctCount": correct,
        "completedAt": "2026-08-13T12:00:00+00:00" if completed else None,
        "answers": [{"seconds": str(seconds)} for _ in range(5)],
    }


def test_an_empty_set_reports_zeroes_rather_than_failing():
    out = stats.summarise([])
    assert out["rounds"] == 0 and out["players"] == 0


def test_abandoned_rounds_are_excluded():
    """
    Including them would drag every average toward zero and make a quiet day
    look like a bad one.
    """
    out = stats.summarise([_session(), _session(completed=False)])
    assert out["rounds"] == 1


def test_players_are_counted_distinctly_from_rounds():
    out = stats.summarise([
        _session(identity="u1", quiz_date="2026-08-12"),
        _session(identity="u1", quiz_date="2026-08-13"),
        _session(identity="u2"),
    ])
    assert out["rounds"] == 3
    assert out["players"] == 2


def test_averages_are_over_completed_rounds():
    out = stats.summarise([_session(points=400), _session(points=600)])
    assert out["avgPoints"] == 500


def test_the_best_score_is_the_maximum_not_the_mean():
    out = stats.summarise([_session(points=400), _session(points=900)])
    assert out["bestPoints"] == 900


def test_perfect_rounds_are_counted():
    out = stats.summarise([_session(correct=5), _session(correct=4)])
    assert out["perfectRounds"] == 1


def test_average_seconds_is_per_answer_not_per_round():
    out = stats.summarise([_session(seconds=10.0)])
    assert out["avgSeconds"] == 10.0


def test_unparseable_timings_do_not_break_the_rollup():
    session = _session()
    session["answers"] = [{"seconds": "not-a-number"}, {"seconds": "6.0"}]
    assert stats.summarise([session])["avgSeconds"] == 6.0


def test_a_session_with_no_answers_still_counts_as_a_round():
    session = _session()
    session["answers"] = []
    out = stats.summarise([session])
    assert out["rounds"] == 1 and out["avgSeconds"] == 0


def test_averages_survive_being_stored():
    """
    Regression. DynamoDB rejects Python floats, and averages are the whole
    point of this table - so the nightly job failed on its first real run,
    inside a scheduled task where nobody is watching the response.
    """
    from decimal import Decimal

    stored = stats._storable(stats.summarise([_session(seconds=7.5, correct=4)]))
    for key, value in stored.items():
        assert not isinstance(value, float), f"{key} is still a float"
    assert isinstance(stored["avgSeconds"], Decimal)


def test_storable_leaves_other_types_alone():
    assert stats._storable({"a": 1, "b": "x", "c": [1, 2]}) == {
        "a": 1, "b": "x", "c": [1, 2]}


# ------------------------------------------------------------ per-sport


def _answer(sport=None, correct=True, seconds=8.0):
    return {"seconds": str(seconds), "sport": sport, "correct": correct}


def _sported(answers, identity="u1", quiz_date="2026-08-13"):
    return {
        "identity": identity,
        "quizDate": quiz_date,
        "totalPoints": 500,
        "correctCount": sum(1 for a in answers if a.get("correct")),
        "completedAt": "2026-08-13T12:00:00+00:00",
        "answers": answers,
    }


def test_accuracy_is_tallied_per_sport():
    out = stats.summarise([_sported([
        _answer("mlb", True), _answer("mlb", False),
        _answer("nhl", True),
    ])])
    assert out["bySport"]["mlb"] == {"asked": 2, "correct": 1, "accuracy": 0.5}
    assert out["bySport"]["nhl"]["accuracy"] == 1.0


def test_answers_recorded_before_sport_was_stored_are_skipped():
    """
    Not bucketed under "unknown": early rounds carry no sport, and a bucket
    that large would dominate the chart while saying nothing.
    """
    out = stats.summarise([_sported([_answer(None, True), _answer("mlb", True)])])
    assert list(out["bySport"]) == ["mlb"]


def test_a_set_with_no_sports_at_all_reports_an_empty_breakdown():
    out = stats.summarise([_sported([_answer(None), _answer(None)])])
    assert out["bySport"] == {}


def test_an_empty_set_still_carries_the_breakdown_key():
    """The shape must not change with the data, or every reader needs a guard."""
    assert stats.summarise([])["bySport"] == {}
