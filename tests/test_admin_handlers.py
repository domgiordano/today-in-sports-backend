"""
Admin handler tests.

Phase 1 has no public routes, so every handler here sits behind the admin gate.
The gate is the first thing tested for each: a route that quietly serves an
unauthenticated caller is worse than one that is broken.

Dynamo access is stubbed. These test the handler contract — auth, validation,
and what the handler refuses to do — not boto3.
"""

import json
import os

import pytest

os.environ.setdefault("ADMIN_EMAIL", "dom@example.com")

from lambdas.common import constants  # noqa: E402

constants.ADMIN_EMAIL = "dom@example.com"

from lambdas.admin_bank_coverage import handler as coverage_h      # noqa: E402
from lambdas.admin_questions_list import handler as list_h         # noqa: E402
from lambdas.admin_questions_review import handler as review_h     # noqa: E402
from lambdas.admin_quizzes_update import handler as update_h       # noqa: E402


def event(email="dom@example.com", body=None, query=None, path=None):
    ev = {
        "requestContext": {"authorizer": {"email": email}},
        "queryStringParameters": query,
        "pathParameters": path,
    }
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


def payload(response):
    return json.loads(response["body"])


class TestAdminGate:
    """A non-admin must never reach the data layer."""

    @pytest.mark.parametrize("mod,kwargs", [
        (list_h, {}),
        (coverage_h, {}),
        (review_h, {"body": {"action": "approve", "questionId": "q1"}}),
    ])
    def test_non_admin_is_refused(self, mod, kwargs, monkeypatch):
        def explode(*_a, **_k):
            raise AssertionError("handler reached the data layer for a non-admin")

        monkeypatch.setattr("lambdas.common.questions_dynamo.list_by_status", explode)
        monkeypatch.setattr("lambdas.common.questions_dynamo.coverage_counts", explode)
        monkeypatch.setattr("lambdas.common.questions_dynamo.set_status", explode)

        resp = mod.handler(event(email="someone@else.com", **kwargs), None)
        assert resp["statusCode"] in (401, 403)

    def test_unauthenticated_is_refused(self):
        resp = list_h.handler({"requestContext": {}}, None)
        assert resp["statusCode"] in (401, 403)


class TestQuestionsList:
    def test_returns_the_bank(self, monkeypatch):
        rows = [{"questionId": "q1", "status": "draft", "mmdd": "08-13"}]
        monkeypatch.setattr("lambdas.common.questions_dynamo.list_by_status",
                            lambda *a, **k: (rows, None))
        resp = list_h.handler(event(query={"status": "draft"}), None)
        assert resp["statusCode"] == 200
        body = payload(resp)
        assert body["count"] == 1
        assert body["questions"][0]["questionId"] == "q1"

    def test_defaults_to_draft(self, monkeypatch):
        seen = {}

        def capture(status, mmdd=None, limit=100, last_key=None):
            seen["status"] = status
            return [], None

        monkeypatch.setattr("lambdas.common.questions_dynamo.list_by_status", capture)
        list_h.handler(event(), None)
        assert seen["status"] == "draft"


class TestReview:
    def test_approve(self, monkeypatch):
        calls = {}

        def set_status(qid, status, reviewer, reason=None):
            calls.update(qid=qid, status=status, reviewer=reviewer, reason=reason)
            return {"questionId": qid, "status": status}

        monkeypatch.setattr("lambdas.common.questions_dynamo.set_status", set_status)
        resp = review_h.handler(
            event(body={"action": "approve", "questionId": "q1"}), None)

        assert resp["statusCode"] == 200
        assert calls["status"] == "approved"
        assert calls["reviewer"] == "dom@example.com"

    def test_rejection_without_a_reason_is_refused(self, monkeypatch):
        """
        The reason is the only signal for fixing the template or detector that
        produced a bad question. A rejection without one teaches nothing.
        """
        def explode(*_a, **_k):
            raise AssertionError("rejected without a reason")

        monkeypatch.setattr("lambdas.common.questions_dynamo.set_status", explode)
        resp = review_h.handler(
            event(body={"action": "reject", "questionId": "q1"}), None)
        assert resp["statusCode"] == 400

    def test_unknown_action_is_refused(self, monkeypatch):
        resp = review_h.handler(
            event(body={"action": "delete", "questionId": "q1"}), None)
        assert resp["statusCode"] == 400

    def test_missing_question_id_is_refused(self):
        resp = review_h.handler(event(body={"action": "approve"}), None)
        assert resp["statusCode"] == 400


class TestCoverage:
    def test_reports_empty_and_thin_dates(self, monkeypatch):
        counts = {"08-13": 20, "08-14": 3, "12-25": 1}
        monkeypatch.setattr("lambdas.common.questions_dynamo.coverage_counts",
                            lambda status="approved": counts)
        body = payload(coverage_h.handler(event(), None))

        assert body["datesCovered"] == 3
        assert body["datesEmpty"] == 363
        assert body["datesThin"] == 2          # under the 15-per-date target
        assert body["datesUnderQuizLength"] == 2   # under five
        assert body["total"] == 24


class TestQuizUpdate:
    def test_publishing_marks_questions_used(self, monkeypatch):
        """
        Publishing is what stops a question resurfacing on this calendar date in
        a later year. If it does not mark them, returning players see repeats.
        """
        marked = {}
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.set_status",
                            lambda d, s: {"quizDate": d, "status": s,
                                          "questionIds": ["a", "b", "c", "d", "e"]})
        monkeypatch.setattr("lambdas.common.questions_dynamo.mark_used",
                            lambda ids, d: marked.update(ids=ids, date=d))

        resp = update_h.handler(
            event(body={"action": "status", "status": "published"},
                  path={"date": "2026-08-13"}), None)

        assert resp["statusCode"] == 200
        assert marked["ids"] == ["a", "b", "c", "d", "e"]
        assert marked["date"] == "2026-08-13"

    def test_setting_draft_does_not_mark_used(self, monkeypatch):
        def explode(*_a, **_k):
            raise AssertionError("marked used on a non-publish transition")

        monkeypatch.setattr("lambdas.common.quizzes_dynamo.set_status",
                            lambda d, s: {"quizDate": d, "status": s,
                                          "questionIds": ["a"]})
        monkeypatch.setattr("lambdas.common.questions_dynamo.mark_used", explode)

        resp = update_h.handler(
            event(body={"action": "status", "status": "draft"},
                  path={"date": "2026-08-13"}), None)
        assert resp["statusCode"] == 200
