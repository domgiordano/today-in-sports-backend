"""
The daily mail.

What is pinned here is not the markup — that will change — but the two things
that would be quietly wrong: an answer reaching an inbox before the quiz is
over, and a question's own text being able to write HTML into the message.
"""

from lambdas.common.email_render import daily_digest

QUESTIONS = [
    {"tier": 1, "sport": "mlb", "prompt": "Who won?", "answer": "The Cubs",
     "sourceUrl": "https://retrosheet.org/"},
    {"tier": 5, "sport": "f1", "prompt": "Where was it?", "answer": ["Spa", "Monza"]},
]


def test_a_review_request_carries_the_answers():
    out = daily_digest("2026-08-15", QUESTIONS, state="proposed")
    assert "The Cubs" in out["html"]
    assert "The Cubs" in out["text"]


def test_a_published_day_never_carries_them():
    """
    The quiz is live and identical for everybody, so an answer sitting in an
    inbox is a spoiler for anyone who has not played yet.
    """
    out = daily_digest("2026-08-14", QUESTIONS, state="published")
    assert "The Cubs" not in out["html"]
    assert "The Cubs" not in out["text"]
    assert "Spa" not in out["html"]


def test_the_subject_says_which_kind_of_mail_it_is():
    review = daily_digest("2026-08-15", QUESTIONS, state="proposed")["subject"]
    live = daily_digest("2026-08-14", QUESTIONS, state="published")["subject"]
    assert "review" in review and "2026-08-15" in review
    assert "live" in live and "2026-08-14" in live


def test_the_subject_counts_one_question_in_the_singular():
    out = daily_digest("2026-08-15", QUESTIONS[:1], state="proposed")
    assert "1 question to review" in out["subject"]


def test_a_prompt_cannot_write_markup_into_the_message():
    """Prompts are data, and one of them will eventually contain a bracket."""
    nasty = [{"tier": 1, "sport": "mlb", "answer": "x",
              "prompt": '<script>alert("x")</script> & "quoted"'}]
    html = daily_digest("2026-08-15", nasty, state="proposed")["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_an_empty_day_says_so_rather_than_looking_finished():
    out = daily_digest("2026-08-20", [], state="proposed")
    assert "Nothing is assembled" in out["html"]
    assert "Nothing is assembled" in out["text"]


def test_both_bodies_are_always_built():
    """HTML-only mail scores worse with filters and is unreadable on a watch."""
    out = daily_digest("2026-08-15", QUESTIONS, state="proposed")
    assert out["html"].startswith("<!doctype html>")
    assert out["text"].startswith("TODAY IN SPORTS")
    # The plain part is written, not stripped, so no markup should survive into it.
    assert "<td" not in out["text"] and "<div" not in out["text"]


def test_a_list_answer_reads_as_a_sentence():
    out = daily_digest("2026-08-15", QUESTIONS, state="proposed")
    assert "Spa, Monza" in out["html"]
    assert "Spa, Monza" in out["text"]
