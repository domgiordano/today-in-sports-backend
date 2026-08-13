"""
Question bank data access.

The bank is the review surface: questions arrive as `draft`, a human moves them
to `approved` or `rejected`, and the assembler marks them `used` once they ship
on a date. Nothing else may change status.
"""

from datetime import datetime, timezone

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
    return _dynamo.Table(constants.QUESTIONS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_question(question_id):
    resp = _table().get_item(Key={"questionId": question_id})
    return resp.get("Item")


def list_by_status(status, mmdd=None, limit=100, last_key=None):
    """
    Query the status index.

    `mmdd` narrows to a single calendar date, which is how the review queue is
    worked when filling a specific gap.
    """
    cond = Key("status").eq(status)
    if mmdd:
        cond = cond & Key("mmdd").eq(mmdd)

    kwargs = {
        "IndexName": constants.QUESTIONS_STATUS_INDEX,
        "KeyConditionExpression": cond,
        "Limit": min(int(limit), 500),
    }
    if last_key:
        kwargs["ExclusiveStartKey"] = last_key

    resp = _table().query(**kwargs)
    return resp.get("Items", []), resp.get("LastEvaluatedKey")


def list_bank(status="approved", sport=None, tier=None, limit=200):
    """Approved inventory, optionally narrowed to one sport-and-tier slot."""
    cond = Key("status").eq(status)
    if sport and tier:
        cond = cond & Key("sportTier").eq(f"{sport}#{tier}")
    elif sport:
        cond = cond & Key("sportTier").begins_with(f"{sport}#")

    resp = _table().query(
        IndexName=constants.QUESTIONS_BANK_INDEX,
        KeyConditionExpression=cond,
        Limit=min(int(limit), 1000),
    )
    return resp.get("Items", [])


def set_status(question_id, status, reviewer, reason=None):
    """
    Record a review decision.

    A rejection reason is required — it is the only signal for fixing the
    template or detector that produced a bad question, and rejections without
    one teach nothing.
    """
    if status not in constants.VALID_QUESTION_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if status == "rejected" and not reason:
        raise ValueError("a rejection requires a reason")

    expr = ["#s = :s", "reviewedAt = :t", "reviewedBy = :who"]
    names = {"#s": "status"}
    values = {":s": status, ":t": _now(), ":who": reviewer}

    if reason:
        expr.append("rejectionReason = :r")
        values[":r"] = reason

    resp = _table().update_item(
        Key={"questionId": question_id},
        UpdateExpression="SET " + ", ".join(expr),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    log.info(f"question {question_id} -> {status}")
    return resp.get("Attributes")


def apply_edit(question_id, fields, reviewer):
    """
    Edit a question in review.

    Provenance is immutable. Editing wording is fine; re-pointing a question at
    a different source would sever the link that makes it verifiable, so those
    fields are not editable.
    """
    editable = {"prompt", "answer", "distractors", "numericAnswer",
                "tolerance", "tier"}
    updates = {k: v for k, v in (fields or {}).items() if k in editable}
    if not updates:
        raise ValueError("no editable fields supplied")

    names = {f"#f{i}": k for i, k in enumerate(updates)}
    values = {f":v{i}": v for i, v in enumerate(updates.values())}
    sets = [f"{n} = :v{i}" for i, n in enumerate(names)]
    sets += ["editedAt = :t", "editedBy = :who"]
    values[":t"] = _now()
    values[":who"] = reviewer

    resp = _table().update_item(
        Key={"questionId": question_id},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def mark_used(question_ids, quiz_date):
    """Flag questions as shipped, so they never resurface on this date."""
    table = _table()
    for qid in question_ids:
        table.update_item(
            Key={"questionId": qid},
            UpdateExpression=(
                "SET #s = :used, usedOn = list_append("
                "if_not_exists(usedOn, :empty), :d)"),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":used": "used", ":d": [quiz_date], ":empty": []},
        )


def coverage_counts(status="approved"):
    """Approved-question counts per calendar date, for the heatmap."""
    counts = {}
    last_key = None
    while True:
        kwargs = {
            "IndexName": constants.QUESTIONS_STATUS_INDEX,
            "KeyConditionExpression": Key("status").eq(status),
            "ProjectionExpression": "mmdd",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _table().query(**kwargs)
        for item in resp.get("Items", []):
            counts[item["mmdd"]] = counts.get(item["mmdd"], 0) + 1
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return counts
