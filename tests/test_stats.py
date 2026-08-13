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
