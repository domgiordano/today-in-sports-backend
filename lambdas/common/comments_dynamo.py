"""
What a group says about a day's results.

One thread per group per day. Membership is the permission throughout — these
are private groups of at most fifty people who invited each other, which is why
this needs no reporting flow or moderation queue: the group is the moderation.
What it does need is that nobody outside can read or write, and that is checked
by the handlers on every call.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

MAX_LENGTH = 500

# @handle, matching what a username may contain, bounded to the same 20
# characters a handle can be so a wall of text cannot be parsed as one.
#
# The lookbehind is what stops an email address being read as a mention:
# "sam@example.com" would otherwise resolve @example, which is harmless only
# until somebody in the group actually holds that handle. A mention starts a
# word or it is not a mention.
_MENTION = re.compile(r"(?<![\w.@])@([a-z0-9_]{3,20})", re.IGNORECASE)

# Enough to address a small group, few enough that a comment cannot be turned
# into a way of notifying fifty people at once.
MAX_MENTIONS = 10
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


def find_mentions(body, handle_to_user):
    """
    The users a comment addresses, resolved against who is actually here.

    Only group members are mentionable. An @handle belonging to somebody
    outside the group resolves to nothing rather than to them — otherwise a
    private group becomes a way to reach anybody on the app whose handle you
    can guess, which is the opposite of what a private group is.

    Order is preserved and duplicates dropped, so mentioning somebody twice
    addresses them once.
    """
    seen = []
    for raw in _MENTION.findall(body or ""):
        user_id = handle_to_user.get(raw.lower())
        if user_id and user_id not in seen:
            seen.append(user_id)
        if len(seen) >= MAX_MENTIONS:
            break
    return seen


def post(group_id, quiz_date, author_id, body, mentions=None):
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
    if mentions:
        item["mentions"] = list(mentions)
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
