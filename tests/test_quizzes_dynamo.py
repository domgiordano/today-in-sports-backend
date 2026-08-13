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
