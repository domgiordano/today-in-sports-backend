"""
Play sessions.

Everyone gets the identical daily quiz, so the answers leak the moment they
reach the client. The whole design follows from that:

  * Questions are served **one at a time**. Shipping all five up front puts
    every answer in the network tab, whatever the UI chooses to render.
  * Answers are **never** sent before submission. The client posts a choice, the
    server grades it and returns the verdict.
  * The **server** stamps when a question was served and when the answer
    arrived. The client's clock is a suggestion.
  * The client never posts a score. It posts a choice; the score is computed
    here from graded answers and server timestamps.
  * **One attempt per day per identity**, enforced by the session key.

Anonymous replay is still possible — clear storage, play again — and that is
accepted rather than fought. It is made worthless instead: an anonymous player
sees where they would have ranked but does not post to the global board.
"""

import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

# Sessions are transient — a day's play, kept a while for support and stats,
# then expired by TTL rather than accumulating forever.
SESSION_TTL_DAYS = 90

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.PLAYS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def session_key(identity, quiz_date):
    """
    One session per identity per day.

    `identity` is a Cognito sub for a signed-in player, or a device id for an
    anonymous one. Both are opaque strings here; the difference matters for
    leaderboard eligibility, not for play.
    """
    return f"{identity}#{quiz_date}"


def get_session(identity, quiz_date):
    resp = _table().get_item(Key={"playId": session_key(identity, quiz_date)})
    return resp.get("Item")


def start_session(identity, quiz_date, question_ids, anonymous):
    """
    Create a session, or return the existing one.

    Returning the existing session rather than erroring is what makes a refresh
    mid-quiz harmless: the player resumes where they were instead of losing
    their answers or getting a second attempt.
    """
    existing = get_session(identity, quiz_date)
    if existing:
        return existing, False

    now = _now()
    item = {
        "playId": session_key(identity, quiz_date),
        "identity": identity,
        "quizDate": quiz_date,
        "questionIds": list(question_ids),
        "anonymous": bool(anonymous),
        "answers": [],
        "currentIndex": 0,
        "totalPoints": 0,
        "correctCount": 0,
        "startedAt": _iso(now),
        "servedAt": None,
        "completedAt": None,
        "ttl": int((now + timedelta(days=SESSION_TTL_DAYS)).timestamp()),
    }
    # Refuse to create a second session for the same identity and day, even if
    # two requests race.
    _table().put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(playId)",
    )
    return item, True


def mark_served(identity, quiz_date, index):
    """
    Stamp when a question was handed to the player.

    This is the start of the clock, recorded server-side. Without it there is no
    honest way to score time at all.
    """
    resp = _table().update_item(
        Key={"playId": session_key(identity, quiz_date)},
        UpdateExpression="SET servedAt = :t, currentIndex = :i",
        ExpressionAttributeValues={":t": _iso(_now()), ":i": int(index)},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def elapsed_since_served(session):
    """Seconds between serving the question and now, or None if never served."""
    served = session.get("servedAt")
    if not served:
        return None
    try:
        started = datetime.fromisoformat(served)
    except ValueError:
        return None
    return max(0.0, (_now() - started).total_seconds())


def record_answer(identity, quiz_date, index, submitted, result):
    """Append a graded answer and advance the running totals."""
    entry = {
        "index": int(index),
        "submitted": str(submitted) if submitted is not None else None,
        "correct": bool(result["correct"]),
        "credit": str(result["credit"]),
        "points": int(result["points"]),
        "seconds": str(result["seconds"]) if result["seconds"] is not None else None,
        "answeredAt": _iso(_now()),
    }

    resp = _table().update_item(
        Key={"playId": session_key(identity, quiz_date)},
        UpdateExpression=(
            "SET answers = list_append(if_not_exists(answers, :empty), :a), "
            "totalPoints = if_not_exists(totalPoints, :zero) + :p, "
            "correctCount = if_not_exists(correctCount, :zero) + :c, "
            "currentIndex = :next, servedAt = :cleared"
        ),
        ExpressionAttributeValues={
            ":a": [entry],
            ":empty": [],
            ":zero": 0,
            ":p": int(result["points"]),
            ":c": 1 if result["correct"] else 0,
            ":next": int(index) + 1,
            ":cleared": None,
        },
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def complete_session(identity, quiz_date):
    resp = _table().update_item(
        Key={"playId": session_key(identity, quiz_date)},
        UpdateExpression="SET completedAt = :t",
        ExpressionAttributeValues={":t": _iso(_now())},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def is_complete(session):
    return bool(session.get("completedAt")) or \
        int(session.get("currentIndex", 0)) >= len(session.get("questionIds") or [])


def already_answered(session, index):
    return any(int(a.get("index", -1)) == int(index)
               for a in (session.get("answers") or []))
