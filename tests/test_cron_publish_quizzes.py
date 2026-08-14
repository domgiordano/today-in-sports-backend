"""
The publisher, and the four ways a day is allowed to be refused.
"""

from lambdas.cron_publish_quizzes.handler import publishable


def quiz(ids, quiz_date="2026-09-20"):
    return {"quizDate": quiz_date, "questionIds": ids, "status": "draft"}


def bank(*ids, status="approved"):
    return {i: {"questionId": i, "status": status} for i in ids}


def test_a_complete_approved_quiz_publishes():
    ok, reason = publishable(quiz(["a", "b", "c", "d", "e"]),
                             bank("a", "b", "c", "d", "e"))
    assert ok and reason == ""


def test_a_short_quiz_is_refused():
    ok, reason = publishable(quiz(["a", "b"]), bank("a", "b"))
    assert not ok
    assert "2 questions" in reason


def test_a_quiz_pointing_at_a_deleted_question_is_refused():
    """
    A quiz that resolves to nothing is worse than a missing quiz: it fails at
    the player rather than at the schedule. Question ids hash their own answer,
    so correcting an answer really does retire a row.
    """
    ok, reason = publishable(quiz(["a", "b", "c", "d", "gone"]),
                             bank("a", "b", "c", "d"))
    assert not ok
    assert "no longer exist" in reason


def test_a_question_rejected_after_assembly_does_not_ship():
    partial = bank("a", "b", "c", "d")
    partial["bad"] = {"questionId": "bad", "status": "rejected"}
    ok, reason = publishable(quiz(["a", "b", "c", "d", "bad"]), partial)
    assert not ok
    assert "not approved" in reason


def test_a_repeated_question_is_refused():
    ok, reason = publishable(quiz(["a", "a", "c", "d", "e"]),
                             bank("a", "c", "d", "e"))
    assert not ok
    assert "twice" in reason


def test_an_already_used_question_still_publishes():
    """`used` is what publishing itself sets, so a re-run must not refuse."""
    ok, _ = publishable(quiz(["a", "b", "c", "d", "e"]),
                        bank("a", "b", "c", "d", "e", status="used"))
    assert ok
