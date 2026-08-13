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
accepted rather than fought. A daily board is low stakes, and refusing to show a
visitor their own result costs more than the cheating does. The device id makes
casual replay inconvenient, which is the honest level of protection here.

What an account buys is persistence: a profile, a streak, and a history that
survives clearing a browser. That is the real incentive to sign up, rather than
withholding the leaderboard.
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


def record_hint(identity, quiz_date, index):
    """
    Record that the player took the multiple-choice hint on this question.

    Server-side, because the client must never be the source of truth for
    something that changes the score. The options are only released through the
    endpoint that calls this, so asking for them and admitting to asking are the
    same action and cannot be separated.

    Idempotent: asking twice is one hint, not two penalties.
    """
    resp = _table().update_item(
        Key={"playId": session_key(identity, quiz_date)},
        UpdateExpression="ADD hintsUsed :i",
        ExpressionAttributeValues={":i": {int(index)}},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def hint_used(session, index):
    return int(index) in set(session.get("hintsUsed") or set())


def record_clue(identity, quiz_date, index):
    """
    Take one more rung of a clue ladder.

    Counted rather than flagged, because the ladder decays per clue. Server-side
    for the same reason the multiple-choice hint is: the clue is released by the
    call that increments this, so taking one and being charged for it are the
    same action.

    Appended rather than added to a set: a set deduplicates, so taking three
    rungs on one question would have counted as one and the ladder would have
    decayed no further than its first step.
    """
    resp = _table().update_item(
        Key={"playId": session_key(identity, quiz_date)},
        UpdateExpression=(
            "SET cluesTaken = list_append(if_not_exists(cluesTaken, :empty), :i)"
        ),
        ExpressionAttributeValues={":i": [str(int(index))], ":empty": []},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def clues_taken(session, index):
    """How many extra rungs have been paid for on this question."""
    taken = session.get("cluesTaken") or []
    return sum(1 for entry in taken if str(entry).split("#")[0] == str(index))


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


# --------------------------------------------------------------- identity

MAX_NAME_LENGTH = 24


def set_display_name(identity, quiz_date, name):
    """
    Attach a name to a finished session so it can appear on the board.

    Only allowed once the quiz is complete — a name set mid-quiz would let a
    player see how they were doing before deciding whether to be identified.
    """
    clean = (name or "").strip()[:MAX_NAME_LENGTH]
    if not clean:
        raise ValueError("a display name cannot be empty")

    resp = _table().update_item(
        Key={"playId": session_key(identity, quiz_date)},
        UpdateExpression="SET displayName = :n",
        ConditionExpression="attribute_exists(playId) AND attribute_exists(completedAt)",
        ExpressionAttributeValues={":n": clean},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def leaderboard(quiz_date, limit=50):
    """
    Top scores for a day, highest first.

    Reads the quizDate-totalPoints index, so this is one query against a single
    partition rather than a scan. At real volume that partition becomes a write
    hotspot and needs sharding across ~10 keys with a merge on read — noted
    rather than built, because sharding an empty board is premature.
    """
    resp = _table().query(
        IndexName="quizDate-totalPoints-index",
        KeyConditionExpression=Key("quizDate").eq(quiz_date),
        ScanIndexForward=False,
        Limit=min(int(limit), 200),
    )
    rows = [r for r in resp.get("Items", []) if r.get("completedAt")]
    return rows


def sessions_for(identities, quiz_date):
    """
    Completed sessions for named players on a day, highest score first.

    Deliberately not "filter the global board": that query is capped at 200
    rows, so a group member sitting outside the global top 200 would silently
    vanish from their own group's board - the smaller the group, the more
    likely, which is exactly backwards. A group is at most fifty people, so
    fetching each session by key is both exact and cheap.
    """
    rows = []
    for identity in identities or ():
        session = get_session(identity, quiz_date)
        if session and session.get("completedAt"):
            rows.append(session)
    rows.sort(key=lambda r: -int(r.get("totalPoints") or 0))
    return rows


def rank_for(quiz_date, total_points):
    """
    How many finished players scored higher.

    Exact rank is fine while the board is small. Past a few thousand players a
    day this wants a bucketed histogram with atomic counters, reporting a
    percentile instead — exact global rank is expensive and nobody needs it.
    """
    higher, key = 0, None
    while True:
        kwargs = {
            "IndexName": "quizDate-totalPoints-index",
            "KeyConditionExpression": (
                Key("quizDate").eq(quiz_date)
                & Key("totalPoints").gt(int(total_points))
            ),
            "Select": "COUNT",
        }
        if key:
            kwargs["ExclusiveStartKey"] = key
        resp = _table().query(**kwargs)
        higher += resp.get("Count", 0)
        key = resp.get("LastEvaluatedKey")
        if not key:
            break
    return higher + 1
