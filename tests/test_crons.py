"""
Recurring pipeline tests.

Two properties matter more than anything else these handlers do:

  * The assemble cron must never disturb a day a human has signed off. It runs
    unattended every month, and publishing is irreversible — it marks questions
    used so they can never resurface on that calendar date.
  * Neither cron may promote anything to `approved`. Everything they produce is
    a draft; a schedule does not get to decide what players see.
"""

import json

import pytest

from lambdas.cron_assemble_quizzes import handler as assemble_h


def q(qid, tier, sport="mlb", mmdd="08-13", status="approved"):
    return {"questionId": qid, "tier": tier, "sport": sport, "mmdd": mmdd,
            "status": status, "notabilityScore": 80, "type": "mc",
            "prompt": f"question {qid}"}


def payload(response):
    return json.loads(response["body"])


class TestAssembleCron:
    def test_never_overwrites_a_published_or_scheduled_day(self, monkeypatch):
        """
        The one thing an unattended monthly job must not do. Publishing marks
        questions used; reassembling over it would either duplicate or orphan
        them.
        """
        written = []

        monkeypatch.setattr("lambdas.common.questions_dynamo.list_bank",
                            lambda *a, **k: [q(f"q{t}", t) for t in range(1, 6)])
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz",
                            lambda d: {"quizDate": d, "status": "published"})
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.used_question_ids",
                            lambda mmdd: set())
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.put_draft",
                            lambda item: written.append(item))
        monkeypatch.setattr(assemble_h, "_runs_table", lambda: _FakeTable())

        body = payload(assemble_h.handler({"daysAhead": 5}, None))

        assert written == [], "an unattended job overwrote a signed-off day"
        assert body["proposed"] == 0
        assert body["skipped"] == 5

    def test_proposes_drafts_for_untouched_days(self, monkeypatch):
        written = []
        monkeypatch.setattr("lambdas.common.questions_dynamo.list_bank",
                            lambda *a, **k: [q(f"q{t}", t, mmdd="01-01")
                                             for t in range(1, 6)])
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz", lambda d: None)
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.used_question_ids",
                            lambda mmdd: set())
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.put_draft",
                            lambda item: written.append(item))
        monkeypatch.setattr(assemble_h, "_runs_table", lambda: _FakeTable())

        body = payload(assemble_h.handler({"daysAhead": 3}, None))

        assert body["proposed"] == 3
        assert len(written) == 3
        assert all(w["status"] == "draft" for w in written), \
            "a cron must not publish"

    def test_reports_thin_dates_rather_than_hiding_them(self, monkeypatch):
        """
        A short day is a content gap that needs more inventory. Silently
        shipping four questions would look like success.
        """
        monkeypatch.setattr("lambdas.common.questions_dynamo.list_bank",
                            lambda *a, **k: [q("only", 1, mmdd="01-01")])
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz", lambda d: None)
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.used_question_ids",
                            lambda mmdd: set())
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.put_draft", lambda item: None)
        monkeypatch.setattr(assemble_h, "_runs_table", lambda: _FakeTable())

        body = payload(assemble_h.handler({"daysAhead": 2}, None))
        assert body["incomplete"] == 2
        assert len(body["thinDates"]) == 2

    def test_respects_questions_already_used_on_that_calendar_date(self, monkeypatch):
        """The no-repeat guarantee has to hold when the job is unattended too."""
        seen = {}
        monkeypatch.setattr("lambdas.common.questions_dynamo.list_bank",
                            lambda *a, **k: [q(f"q{t}", t, mmdd="01-01")
                                             for t in range(1, 6)])
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz", lambda d: None)
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.used_question_ids",
                            lambda mmdd: seen.setdefault(mmdd, {"q1", "q2"}))
        written = []
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.put_draft",
                            lambda item: written.append(item))
        monkeypatch.setattr(assemble_h, "_runs_table", lambda: _FakeTable())

        assemble_h.handler({"daysAhead": 1}, None)
        assert written
        assert not ({"q1", "q2"} & set(written[0]["questionIds"]))


class TestIngestCron:
    def test_lookback_overlaps_so_a_failed_run_self_heals(self):
        """
        Eight days for a weekly job is deliberate: one run can fail entirely and
        the next still covers the gap.
        """
        from lambdas.cron_ingest_recent import handler as ingest_h
        assert ingest_h.DEFAULT_LOOKBACK_DAYS > 7

    def test_dates_are_recent_and_exclude_today(self):
        """Today's games may still be in progress."""
        from datetime import datetime, timezone
        from lambdas.cron_ingest_recent import handler as ingest_h

        days = ingest_h._dates(8)
        today = datetime.now(timezone.utc).date().isoformat()
        assert len(days) == 8
        assert today not in days
        assert days == sorted(days, reverse=True)


class _FakeTable:
    def put_item(self, **kwargs):
        return {}
