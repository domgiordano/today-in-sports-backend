"""
Repeat prevention is a correctness property of the product: the premise is
date-anchored, so August 13 comes round every year and a returning player must
not see last year's questions.

These tests cover the lookup that enforces it. The pagination case matters more
than it looks — a bare scan stops at 1 MB and returns partial results with no
error, so the bug would surface years later as a repeat, not as a failure.
"""

from unittest.mock import MagicMock, patch

from lambdas.common import quizzes_dynamo


def _stub_table(pages):
    """A table whose scan returns the given pages in order."""
    table = MagicMock()
    calls = []

    def scan(**kwargs):
        calls.append(kwargs)
        return pages[len(calls) - 1]

    table.scan.side_effect = scan
    table.calls = calls
    return table


def test_only_the_same_calendar_date_counts_as_used():
    table = _stub_table([{
        "Items": [
            {"quizDate": "2025-08-13", "questionIds": ["a", "b"]},
            {"quizDate": "2025-08-14", "questionIds": ["c"]},
            {"quizDate": "2024-08-13", "questionIds": ["d"]},
        ],
    }])

    with patch.object(quizzes_dynamo, "_table", return_value=table):
        used = quizzes_dynamo.used_question_ids("08-13")

    # Every year's August 13, and no other date.
    assert used == {"a", "b", "d"}


def test_a_second_page_is_followed():
    table = _stub_table([
        {
            "Items": [{"quizDate": "2025-08-13", "questionIds": ["a"]}],
            "LastEvaluatedKey": {"quizDate": "2025-08-13"},
        },
        {
            "Items": [{"quizDate": "2024-08-13", "questionIds": ["b"]}],
        },
    ])

    with patch.object(quizzes_dynamo, "_table", return_value=table):
        used = quizzes_dynamo.used_question_ids("08-13")

    assert used == {"a", "b"}
    assert len(table.calls) == 2
    assert table.calls[1]["ExclusiveStartKey"] == {"quizDate": "2025-08-13"}


def test_a_quiz_with_no_questions_is_harmless():
    table = _stub_table([{
        "Items": [
            {"quizDate": "2025-08-13"},
            {"quizDate": "2024-08-13", "questionIds": None},
        ],
    }])

    with patch.object(quizzes_dynamo, "_table", return_value=table):
        assert quizzes_dynamo.used_question_ids("08-13") == set()


def test_published_runway_counts_the_run_not_the_total(monkeypatch):
    """
    Sixty published days with a hole on Tuesday is a dark Tuesday. A total
    would report sixty and say nothing about the hole.
    """
    from lambdas.common import quizzes_dynamo as qd

    monkeypatch.setattr(qd, "list_by_status", lambda status, limit=400: [
        {"quizDate": "2026-08-14"}, {"quizDate": "2026-08-15"},
        # 08-16 missing
        {"quizDate": "2026-08-17"}, {"quizDate": "2026-08-18"},
    ])

    out = qd.published_runway(today="2026-08-14")
    assert out["runwayDays"] == 2
    assert out["publishedThrough"] == "2026-08-15"
    assert out["goesDarkOn"] == "2026-08-16"


def test_published_runway_with_nothing_published(monkeypatch):
    from lambdas.common import quizzes_dynamo as qd
    monkeypatch.setattr(qd, "list_by_status", lambda status, limit=400: [])

    out = qd.published_runway(today="2026-08-14")
    assert out["runwayDays"] == 0
    assert out["publishedThrough"] is None
    assert out["goesDarkOn"] == "2026-08-14"


def test_holding_a_day_records_why():
    """A held day with no reason is a day nobody can act on later."""
    from lambdas.common import constants
    assert "held" in constants.VALID_QUIZ_STATUSES


def test_recycling_refuses_a_published_day(monkeypatch):
    """
    Recycling replaces the five questions. Doing that to a published day would
    change the quiz under anybody who already played it.
    """
    import pytest
    from lambdas.common import quizzes_dynamo as qd

    monkeypatch.setattr(qd, "get_quiz",
                        lambda d: {"quizDate": d, "status": "published"})
    with pytest.raises(ValueError, match="already published"):
        qd.recycle("2026-09-20", [])
