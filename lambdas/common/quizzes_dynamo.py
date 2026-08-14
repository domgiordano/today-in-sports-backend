"""
Assembled daily quiz data access.

`quizDate` is a UTC yyyy-mm-dd — the day the quiz is served. That is a different
thing from an event's `gameDate`, which is the local date a game was played. The
two must not be conflated.
"""

from datetime import date, datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.QUIZZES_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_quiz(quiz_date):
    return _table().get_item(Key={"quizDate": quiz_date}).get("Item")


def list_by_status(status, limit=90):
    resp = _table().query(
        IndexName=constants.QUIZZES_STATUS_INDEX,
        KeyConditionExpression=Key("status").eq(status),
        Limit=min(int(limit), 400),
    )
    return resp.get("Items", [])


def list_range(start_date, end_date):
    """Scan a window of dates. The table is small — one row per day."""
    resp = _table().scan(
        FilterExpression=Key("quizDate").between(start_date, end_date))
    return sorted(resp.get("Items", []), key=lambda x: x["quizDate"])


def put_draft(item):
    """Write an assembler result. Never overwrites a published quiz."""
    existing = get_quiz(item["quizDate"])
    if existing and existing.get("status") == "published":
        raise ValueError(
            f"{item['quizDate']} is already published; "
            "unpublish before reassembling")

    item = dict(item)
    item.setdefault("status", "draft")
    item["updatedAt"] = _now()
    _table().put_item(Item=item)
    return item


def swap_question(quiz_date, index, question_id):
    quiz = get_quiz(quiz_date)
    if not quiz:
        raise ValueError(f"no quiz for {quiz_date}")
    if quiz.get("status") == "published":
        raise ValueError("a published quiz cannot be edited")

    ids = list(quiz.get("questionIds") or [])
    if not 0 <= index < len(ids):
        raise ValueError(f"index {index} out of range for {len(ids)} questions")

    ids[index] = question_id
    resp = _table().update_item(
        Key={"quizDate": quiz_date},
        UpdateExpression="SET questionIds = :q, updatedAt = :t",
        ExpressionAttributeValues={":q": ids, ":t": _now()},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def set_status(quiz_date, status, reason=None):
    if status not in constants.VALID_QUIZ_STATUSES:
        raise ValueError(f"invalid status: {status}")

    quiz = get_quiz(quiz_date)
    if not quiz:
        raise ValueError(f"no quiz for {quiz_date}")

    if status == "published":
        ids = quiz.get("questionIds") or []
        if len(ids) != constants.QUIZ_LENGTH:
            raise ValueError(
                f"refusing to publish {quiz_date}: {len(ids)} questions, "
                f"expected {constants.QUIZ_LENGTH}")

    values = {":s": status, ":t": _now()}
    expr = "SET #s = :s, updatedAt = :t"
    if status == "published":
        expr += ", publishedAt = :p"
        values[":p"] = _now()
    if status == "held":
        # Why a day was refused, kept on the row. A held day with no reason is
        # a day nobody can act on later, including the person who held it.
        expr += ", heldAt = :h, heldReason = :r"
        values[":h"] = _now()
        values[":r"] = reason or "no reason given"

    resp = _table().update_item(
        Key={"quizDate": quiz_date},
        UpdateExpression=expr,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    log.info(f"quiz {quiz_date} -> {status}")
    return resp.get("Attributes")


def recycle(quiz_date, questions, used_ids=None):
    """
    Reassemble a held day and return it to the queue.

    A denial is usually about the questions rather than the date, so recycling
    builds a fresh five from the bank and drops the day back to `draft`, where
    the publisher will pick it up again. The previous set is not preserved -
    it was refused.
    """
    from lambdas.common.assembler import assemble

    quiz = get_quiz(quiz_date)
    if not quiz:
        raise ValueError(f"no quiz for {quiz_date}")
    if quiz.get("status") == "published":
        raise ValueError(f"{quiz_date} is already published")

    rebuilt = assemble(quiz_date, questions, used_ids=used_ids)
    item = dict(rebuilt)
    item["status"] = "draft"
    item["recycledFrom"] = quiz.get("questionIds") or []
    item["recycledAt"] = _now()
    item["updatedAt"] = _now()
    _table().put_item(Item=item)
    log.info(f"recycled {quiz_date}")
    return item


def published_runway(today=None):
    """
    How many consecutive days from today already have a published quiz.

    This is the number that decides whether the game is playable next month.
    `play_start` refuses anything not published, so the day after this run ends
    the app answers "no published quiz" and there is no quiz at all.

    `cron_publish_quizzes` keeps this ahead of the horizon on its own now, so a
    short runway means something is wrong rather than that somebody forgot -
    days held for review, or a bank too thin to assemble from.

    Counted as a run rather than a total: sixty published days with a hole on
    Tuesday is a dark Tuesday, and a count would report sixty.
    """
    today = today or _now()[:10]
    published = {q["quizDate"] for q in list_by_status("published", limit=400)
                 if q.get("quizDate", "") >= today}

    day, runway = date.fromisoformat(today), 0
    while day.isoformat() in published:
        runway += 1
        day += timedelta(days=1)

    return {
        "runwayDays": runway,
        "publishedThrough": (date.fromisoformat(today)
                             + timedelta(days=runway - 1)).isoformat()
        if runway else None,
        "goesDarkOn": (date.fromisoformat(today)
                       + timedelta(days=runway)).isoformat(),
    }


def used_question_ids(mmdd):
    """
    Every question already used on this calendar date in any year.

    This is what stops a returning player seeing a repeat when August 13 comes
    round again — the whole premise is date-anchored, so a date recurs annually.

    Paginated deliberately. A bare scan returns at most 1 MB and silently stops;
    after a couple of years of quizzes that would quietly start missing older
    entries, and the failure looks like a repeat rather than a bug.
    """
    used = set()
    last_key = None
    while True:
        kwargs = {"ProjectionExpression": "quizDate, questionIds"}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _table().scan(**kwargs)
        for item in resp.get("Items", []):
            if item.get("quizDate", "")[5:] == mmdd:
                used.update(item.get("questionIds") or [])
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return used
