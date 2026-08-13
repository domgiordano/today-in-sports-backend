"""
Play-surface tests.

Everyone gets the identical daily quiz, so most of these check what the server
refuses to do rather than what it returns. The answer leaking into a payload,
or a wrong answer being resubmitted until it is right, would each quietly ruin
the game for every player at once.
"""

import json

import pytest

from lambdas.common import play_view
from lambdas.play_start import handler as start_h


def question(qid="q1", qtype="mc", tier=3):
    return {
        "questionId": qid,
        "type": qtype,
        "tier": tier,
        "prompt": "Who threw a no-hitter on May 1, 1991?",
        "answer": "Nolan Ryan",
        "distractors": ["Stan Belinda", "Bruce Hurst", "Jose Mesa"],
        "sport": "mlb",
        "league": "American League",
        "tolerance": 2,
        "sourceDatasetRef": "https://retrosheet.org/x",
    }


def event(body, sub=None):
    ctx = {"authorizer": {"sub": sub}} if sub else {}
    return {"body": json.dumps(body), "requestContext": ctx}


def payload(response):
    return json.loads(response["body"])


class TestAnswersNeverLeak:
    """
    The single most important property. Everyone plays the same quiz, so an
    answer in the payload is an answer for everyone, whatever the UI renders.
    """

    def test_public_question_omits_the_answer(self):
        pub = play_view.public_question(question(), 0, 5)
        blob = json.dumps(pub)
        assert "answer" not in pub
        assert "distractors" not in pub
        assert "Nolan Ryan" not in blob
        assert '"answer"' not in blob

    def test_options_are_withheld_until_the_hint_is_taken(self):
        """
        The choices are a scored hint, so they cannot ship with the question.
        If the client already held them, whether the player looked would be a
        fact only the client knew, and the score would rest on its honesty.
        """
        pub = play_view.public_question(question(), 0, 5)
        assert pub["options"] is None
        assert pub["hintAvailable"] is True

    def test_options_include_every_choice_exactly_once(self):
        pub = play_view.public_question(question(), 0, 5, with_options=True)
        assert sorted(pub["options"]) == sorted(
            ["Nolan Ryan", "Stan Belinda", "Bruce Hurst", "Jose Mesa"])
        # Once released, there is no further hint to offer.
        assert pub["hintAvailable"] is False

    def test_option_order_is_stable_for_a_question(self):
        """Otherwise a refresh reshuffles and the answer can be inferred."""
        assert play_view.options_for(question()) == play_view.options_for(question())

    def test_numeric_questions_expose_tolerance_but_not_the_number(self):
        pub = play_view.public_question(question(qtype="numeric"), 0, 5)
        assert pub["options"] is None
        assert pub["tolerance"] == 2
        assert "answer" not in pub


class TestQuizDayIsUtc:
    """
    UTC, not local. A per-viewer day would fragment the leaderboard into
    twenty-four overlapping days and let a player see tomorrow's quiz early.
    """

    def test_today_is_utc_not_local(self):
        from datetime import datetime, timezone
        assert play_view.today_utc() == datetime.now(timezone.utc).date().isoformat()


class TestStartRequiresIdentityAndAPublishedQuiz:
    def test_anonymous_without_a_device_id_is_refused(self, monkeypatch):
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz",
                            lambda d: {"status": "published", "questionIds": ["q1"]})
        resp = start_h.handler(event({}), None)
        assert resp["statusCode"] == 400

    def test_a_draft_quiz_is_not_playable(self, monkeypatch):
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz",
                            lambda d: {"status": "draft", "questionIds": ["q1"]})
        resp = start_h.handler(event({"deviceId": "dev-1"}), None)
        assert resp["statusCode"] == 404

    def test_a_missing_quiz_is_a_404(self, monkeypatch):
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz", lambda d: None)
        resp = start_h.handler(event({"deviceId": "dev-1"}), None)
        assert resp["statusCode"] == 404

    def test_a_signed_in_subject_overrides_a_supplied_device_id(self, monkeypatch):
        """A client must not be able to claim someone else's identity."""
        captured = {}
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz",
                            lambda d: {"status": "published", "questionIds": ["q1"]})
        monkeypatch.setattr("lambdas.common.questions_dynamo.get_question",
                            lambda qid: question())

        def start_session(identity, quiz_date, ids, anonymous):
            captured["identity"] = identity
            captured["anonymous"] = anonymous
            return {"currentIndex": 0, "questionIds": ids, "totalPoints": 0,
                    "anonymous": anonymous}, True

        monkeypatch.setattr("lambdas.common.plays_dynamo.start_session", start_session)
        monkeypatch.setattr("lambdas.common.plays_dynamo.mark_served",
                            lambda *a: None)

        start_h.handler(event({"deviceId": "spoofed"}, sub="real-user-sub"), None)
        assert captured["identity"] == "real-user-sub"
        assert captured["anonymous"] is False


class TestSessionRules:
    def test_a_finished_session_reports_complete_rather_than_replaying(self, monkeypatch):
        """One attempt per identity per day."""
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz",
                            lambda d: {"status": "published",
                                       "questionIds": ["q1", "q2"]})
        monkeypatch.setattr(
            "lambdas.common.plays_dynamo.start_session",
            lambda *a: ({"completedAt": "2026-08-13T00:00:00+00:00",
                         "questionIds": ["q1", "q2"], "totalPoints": 420,
                         "correctCount": 4, "currentIndex": 2,
                         "anonymous": True}, False))

        body = payload(start_h.handler(event({"deviceId": "dev-1"}), None))
        assert body["state"] == "complete"
        assert body["totalPoints"] == 420
        assert "question" not in body

    def test_resuming_returns_the_current_question_not_the_first(self, monkeypatch):
        """A refresh mid-quiz must not restart or lose answers."""
        monkeypatch.setattr("lambdas.common.quizzes_dynamo.get_quiz",
                            lambda d: {"status": "published",
                                       "questionIds": ["q1", "q2", "q3"]})
        monkeypatch.setattr(
            "lambdas.common.plays_dynamo.start_session",
            lambda *a: ({"currentIndex": 2, "questionIds": ["q1", "q2", "q3"],
                         "totalPoints": 250, "anonymous": True}, False))
        monkeypatch.setattr("lambdas.common.questions_dynamo.get_question",
                            lambda qid: question(qid=qid))
        monkeypatch.setattr("lambdas.common.plays_dynamo.mark_served", lambda *a: None)

        body = payload(start_h.handler(event({"deviceId": "dev-1"}), None))
        assert body["resumed"] is True
        assert body["question"]["index"] == 2
        assert body["totalPoints"] == 250


class TestSessionHelpers:
    def test_completion_is_detected_by_index_as_well_as_flag(self):
        from lambdas.common import plays_dynamo
        assert plays_dynamo.is_complete(
            {"questionIds": ["a", "b"], "currentIndex": 2}) is True
        assert plays_dynamo.is_complete(
            {"questionIds": ["a", "b"], "currentIndex": 1}) is False

    def test_already_answered_blocks_a_resubmission(self):
        from lambdas.common import plays_dynamo
        session = {"answers": [{"index": 0}, {"index": 1}]}
        assert plays_dynamo.already_answered(session, 1) is True
        assert plays_dynamo.already_answered(session, 2) is False

    def test_elapsed_is_none_when_never_served(self):
        """No serve stamp means no honest clock, and scoring treats that as slow."""
        from lambdas.common import plays_dynamo
        assert plays_dynamo.elapsed_since_served({}) is None
        assert plays_dynamo.elapsed_since_served({"servedAt": "not-a-date"}) is None

    def test_session_key_is_per_identity_per_day(self):
        from lambdas.common import plays_dynamo
        assert plays_dynamo.session_key("a", "2026-08-13") != \
            plays_dynamo.session_key("a", "2026-08-14")
        assert plays_dynamo.session_key("a", "2026-08-13") != \
            plays_dynamo.session_key("b", "2026-08-13")


class TestNoCrossLambdaImports:
    """
    Each Lambda ships with its own folder plus the shared layer, so a handler
    importing from a sibling handler resolves locally and then dies at cold
    start with `No module named 'lambdas.play_start'`. Shared code belongs in
    `lambdas/common`.
    """

    def test_handlers_do_not_import_each_other(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent / "lambdas"
        offenders = []
        for handler in root.glob("*/handler.py"):
            own = handler.parent.name
            for line in handler.read_text().splitlines():
                m = re.match(r"\s*from lambdas\.(\w+)\.handler import", line)
                if m and m.group(1) != own and m.group(1) != "common":
                    offenders.append(f"{own} imports {m.group(1)}")
        assert not offenders, "cross-lambda imports: " + ", ".join(offenders)


class TestMapQuestionsLeakNothing:
    """
    A map question's answer is a coordinate, so the venue name, its town and
    its country are all answers too. None of them may travel with the question.
    """

    def _q(self):
        return {
            "questionId": "m1", "type": "map", "tier": 3,
            "prompt": "Tap where you think the circuit is.",
            "sport": "f1", "league": "Formula One",
            "answer": {"lat": 45.6206, "lng": 9.2894},
            "venueName": "Autodromo Nazionale Monza",
            "venuePlace": "Monza", "venueCountry": "italy",
        }

    def test_no_part_of_the_location_is_served(self):
        blob = json.dumps(play_view.public_question(self._q(), 0, 5))
        for leak in ("45.6", "9.28", "Monza", "Autodromo", "italy"):
            assert leak not in blob, f"{leak} leaked in the question payload"

    def test_the_prompt_still_survives(self):
        pub = play_view.public_question(self._q(), 0, 5)
        assert pub["type"] == "map"
        assert "Tap where" in pub["prompt"]
