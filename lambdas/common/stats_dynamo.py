"""
Precomputed statistics.

Every number the analytics screen shows is rolled up by a nightly job and read
back by key. Computing them per request would mean scanning the plays table on
every page load - which is fine at ten players, imperceptibly slower at a
hundred, and falls over at ten thousand without anybody having changed
anything. That failure mode is the reason this table exists.

Rows are keyed `scope` / `period`:

    scope    "global" | "group#<groupId>" | "region#<country>"
    period   "all" | "week" | "month" | "day#<yyyy-mm-dd>"

so a slice is one GetItem regardless of how much play sits behind it.

**Region is country-level and stored coarsely.** Not county: that needs a paid
database, only means anything in one country, and keeping county-level location
against named accounts is more data than a trivia app can justify holding.
"""

from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

_dynamo = None

VALID_PERIODS = ("all", "week", "month")


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.STATS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc)


def _storable(value):
    """
    DynamoDB rejects Python floats outright.

    Averages are the whole point of this table and averages are floats, so
    without this every nightly run fails - and it fails inside a scheduled job
    where nobody is watching the response.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _storable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_storable(v) for v in value]
    return value


def put_rollup(scope, period, stats):
    item = {
        "scope": scope,
        "period": period,
        "computedAt": _now().isoformat(),
        **stats,
    }
    _table().put_item(Item=_storable(item))
    return item


def get_rollup(scope, period):
    resp = _table().get_item(Key={"scope": scope, "period": period})
    return resp.get("Item")


def list_scope(scope):
    """Every period for one scope, so a screen can show them side by side."""
    resp = _table().query(KeyConditionExpression=Key("scope").eq(scope))
    return {row["period"]: row for row in resp.get("Items", [])}


def summarise(sessions):
    """
    Reduce a set of completed sessions to the numbers worth showing.

    Averages are over completed rounds only. Including abandoned ones would
    drag every average toward zero and make a quiet day look like a bad one.
    """
    completed = [s for s in sessions if s.get("completedAt")]
    if not completed:
        return {
            "rounds": 0, "players": 0, "avgPoints": 0, "avgCorrect": 0,
            "perfectRounds": 0, "avgSeconds": 0, "bestPoints": 0,
            "bySport": {},
        }

    points = [int(s.get("totalPoints") or 0) for s in completed]
    correct = [int(s.get("correctCount") or 0) for s in completed]

    seconds = []
    for s in completed:
        for answer in s.get("answers") or []:
            try:
                seconds.append(float(answer.get("seconds") or 0))
            except (TypeError, ValueError):
                continue

    return {
        "rounds": len(completed),
        "players": len({s.get("identity") for s in completed}),
        "avgPoints": round(sum(points) / len(points)),
        "avgCorrect": round(sum(correct) / len(correct), 2),
        "perfectRounds": len([c for c in correct if c >= constants.QUIZ_LENGTH]),
        "avgSeconds": round(sum(seconds) / len(seconds), 1) if seconds else 0,
        "bestPoints": max(points),
        "bySport": _by_sport(completed),
    }


def _by_sport(completed):
    """
    Accuracy per sport.

    Answers recorded before `sport` was stored carry none, and are skipped
    rather than bucketed under "unknown": a bucket that large would dominate
    the chart and say nothing. It fills in as new rounds are played.
    """
    tally = {}
    for session in completed:
        for answer in session.get("answers") or []:
            sport = answer.get("sport")
            if not sport:
                continue
            bucket = tally.setdefault(sport, {"asked": 0, "correct": 0})
            bucket["asked"] += 1
            if answer.get("correct"):
                bucket["correct"] += 1

    return {
        sport: {
            **counts,
            "accuracy": round(counts["correct"] / counts["asked"], 3),
        }
        for sport, counts in tally.items()
        if counts["asked"]
    }
