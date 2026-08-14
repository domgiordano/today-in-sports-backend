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


def test_a_held_day_is_never_published(monkeypatch):
    """
    Publishing is automatic, so a denial has to be a state the publisher can
    see. A held day treated as just another unpublished one is republished the
    next morning, which is the whole thing `held` exists to prevent.

    Runs the handler rather than restating its filter - a test that
    re-implements the logic passes just as happily when the logic is wrong.
    """
    from datetime import datetime, timedelta, timezone

    from lambdas.cron_publish_quizzes import handler as h

    today = datetime.now(timezone.utc).date()
    day = {n: (today + timedelta(days=n)).isoformat() for n in (1, 2, 3)}
    quizzes = {
        day[1]: {"quizDate": day[1], "status": "held",
                 "questionIds": ["a", "b", "c", "d", "e"]},
        day[2]: {"quizDate": day[2], "status": "draft",
                 "questionIds": ["a", "b", "c", "d", "e"]},
        day[3]: {"quizDate": day[3], "status": "published",
                 "questionIds": ["a", "b", "c", "d", "e"]},
    }

    published = []
    monkeypatch.setattr(h.quizzes_dynamo, "get_quiz", quizzes.get)
    monkeypatch.setattr(h.quizzes_dynamo, "set_status",
                        lambda d, s, **kw: published.append(d))
    monkeypatch.setattr(h.quizzes_dynamo, "published_runway",
                        lambda today=None: {"runwayDays": 1})
    monkeypatch.setattr(h.questions_dynamo, "get_many",
                        lambda ids: bank("a", "b", "c", "d", "e"))
    monkeypatch.setattr(h, "_runs_table", lambda: _NullTable())

    h.handler({"horizonDays": 3}, None)

    assert published == [day[2]], "only the draft should publish"


class _NullTable:
    def put_item(self, **kwargs):
        return {}
