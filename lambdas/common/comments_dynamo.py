"""
What a group says about a day's results.

One thread per group per day. Membership is the permission throughout — these
are private groups of at most fifty people who invited each other, which is why
this needs no reporting flow or moderation queue: the group is the moderation.
What it does need is that nobody outside can read or write, and that is checked
by the handlers on every call.
"""

import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

MAX_LENGTH = 500
# Enough that a day's argument fits, few enough that one person cannot push the
# rest of the group off the page.
MAX_PER_THREAD = 200
TTL_DAYS = 90

_dynamo = None


def _resource():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo


def _table():
    return _resource().Table(constants.COMMENTS_TABLE_NAME)


def thread_id(group_id, quiz_date):
    return f"{group_id}#{quiz_date}"


def post(group_id, quiz_date, author_id, body):
    """
    Add a comment. Returns the stored row.

    Raises ValueError for an empty or over-long body — checked here rather than
    only in the handler so the rule travels with the data rather than with one
    caller of it.
    """
    text = (body or "").strip()
    if not text:
        raise ValueError("A comment needs something in it.")
    if len(text) > MAX_LENGTH:
        raise ValueError(f"Comments are at most {MAX_LENGTH} characters.")

    now = datetime.now(timezone.utc)
    comment_id = uuid.uuid4().hex[:12]
    item = {
        "threadId": thread_id(group_id, quiz_date),
        # Timestamp first so a query returns the day in order; the id appended
        # so two comments in the same millisecond do not collide.
        "postedAtId": f"{now.isoformat()}#{comment_id}",
        "commentId": comment_id,
        "groupId": group_id,
        "quizDate": quiz_date,
        "authorId": author_id,
        "body": text,
        "postedAt": now.isoformat(),
        "ttl": int((now + timedelta(days=TTL_DAYS)).timestamp()),
    }
    _table().put_item(Item=item)
    return item


def for_thread(group_id, quiz_date, limit=MAX_PER_THREAD):
    """A day's comments, oldest first, which is how a conversation reads."""
    resp = _table().query(
        KeyConditionExpression=Key("threadId").eq(thread_id(group_id, quiz_date)),
        ScanIndexForward=True,
        Limit=limit,
    )
    return resp.get("Items", [])


def find(group_id, quiz_date, comment_id):
    for item in for_thread(group_id, quiz_date):
        if item.get("commentId") == comment_id:
            return item
    return None


def delete(group_id, quiz_date, comment_id):
    item = find(group_id, quiz_date, comment_id)
    if not item:
        return False
    _table().delete_item(
        Key={"threadId": item["threadId"], "postedAtId": item["postedAtId"]})
    return True


def may_delete(comment, user_id, group):
    """
    Who can remove a comment: whoever wrote it, and whoever owns the group.

    The owner because somebody has to be able to, and in a group this size that
    is the person who made it. There is no wider moderation because there is no
    wider audience — you cannot see a group you were not invited to.
    """
    return (comment.get("authorId") == user_id
            or group.get("ownerId") == user_id)
