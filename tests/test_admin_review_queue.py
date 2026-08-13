"""
The review queue exists to make review finite. These tests pin the two rules
that do that: dates already carrying enough approved questions produce no work,
and published dates produce no work at all.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lambdas.admin_review_queue import handler as review_queue


def _event(days=None):
    e = {
        "requestContext": {
            "authorizer": {"claims": {"email": "admin@example.com"}}
        }
    }
    if days is not None:
        e["queryStringParameters"] = {"days": str(days)}
    return e


def _question(qid, mmdd):
    return {
        "questionId": qid,
        "mmdd": mmdd,
        "status": "draft",
        "prompt": "who?",
        "sourceDatasetRef": "retrosheet://1991",
    }


@pytest.fixture(autouse=True)
def _admin():
    with patch.object(review_queue, "require_admin", return_value="admin@x"):
        yield


def _run(event, by_status, quizzes=None):
    """Run the handler against stubbed tables and return the parsed body."""
    import json

    quizzes = quizzes or {}

    def list_by_status(status, mmdd=None, limit=100, last_key=None):
        return list(by_status.get((status, mmdd), [])), None

    with patch.object(review_queue.questions_dynamo, "list_by_status",
                      side_effect=list_by_status), \
         patch.object(review_queue.quizzes_dynamo, "get_quiz",
                      side_effect=lambda d: quizzes.get(d)):
        resp = review_queue.handler(event, None)

    return json.loads(resp["body"])


def _mmdd(offset):
    day = datetime.now(timezone.utc).date() + timedelta(days=offset)
    return day.strftime("%m-%d"), day.isoformat()


def test_a_date_with_enough_approved_contributes_no_work():
    mmdd, _ = _mmdd(0)
    approved = [_question(f"a{i}", mmdd)
                for i in range(review_queue.TARGET_PER_DATE)]

    body = _run(_event(days=1), {
        ("approved", mmdd): approved,
        ("draft", mmdd): [_question("d1", mmdd)],
    })

    assert body["count"] == 0
    assert body["shortDates"] == 0
    assert body["dates"][0]["needed"] == 0


def test_a_short_date_offers_its_drafts_tagged_with_what_it_needs():
    mmdd, iso = _mmdd(0)

    body = _run(_event(days=1), {
        ("approved", mmdd): [_question("a1", mmdd)],
        ("draft", mmdd): [_question("d1", mmdd), _question("d2", mmdd)],
    })

    assert body["count"] == 2
    assert body["shortDates"] == 1
    assert body["dates"][0]["needed"] == review_queue.TARGET_PER_DATE - 1
    # The tag is what lets the panel say "For 2026-08-13 · needs 5 more"
    # instead of showing a bare question with no sense of why it is on screen.
    assert body["questions"][0]["_forDate"] == iso
    assert body["questions"][0]["_needed"] == review_queue.TARGET_PER_DATE - 1


def test_a_published_date_is_never_queued_even_when_short():
    """
    Publishing settles a day. Re-reviewing it cannot change what players see,
    so surfacing it would be pure noise in the only queue that matters.
    """
    mmdd, iso = _mmdd(0)

    body = _run(_event(days=1), {
        ("approved", mmdd): [],
        ("draft", mmdd): [_question("d1", mmdd)],
    }, quizzes={iso: {"quizDate": iso, "status": "published"}})

    assert body["count"] == 0
    assert body["shortDates"] == 0
    assert body["dates"][0]["quizStatus"] == "published"


def test_the_window_is_bounded_and_starts_today():
    by_status = {}
    body = _run(_event(days=500), by_status)

    assert body["days"] == review_queue.MAX_DAYS
    assert len(body["dates"]) == review_queue.MAX_DAYS
    assert body["dates"][0]["quizDate"] == \
        datetime.now(timezone.utc).date().isoformat()


def test_the_window_defaults_to_three_weeks():
    body = _run(_event(), {})
    assert body["days"] == review_queue.DEFAULT_DAYS
