"""
What somebody missed.

The hard part of notifications is not storing them, it is deciding who is told.
Get that wrong in the noisy direction and people turn the whole thing off; get
it wrong in the quiet direction and the feature does nothing.

Three rules, and the third is the one doing the work:

  * **mentioned** — somebody wrote your handle. Always.
  * **reaction** — somebody reacted to your round. Always; it is about you.
  * **friend_request** — somebody asked to be your friend. Always; it needs an
    answer from you, which is the strongest case there is for telling somebody.
  * **friend_accepted** — somebody said yes. Always; you asked.
  * **reply** — somebody commented on a day you have already commented on.
    *Not* every comment to every member. In a group of fifty, notifying
    everybody about every comment is fifty notifications for one sentence, and
    the reasonable response to that is to mute the group. You hear about a
    conversation you are in.

Nobody is ever notified about their own action, which sounds obvious and is the
first thing a naive implementation gets wrong.
"""

import uuid
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

MENTION = "mention"
REACTION = "reaction"
REPLY = "reply"
FRIEND_REQUEST = "friend_request"
FRIEND_ACCEPTED = "friend_accepted"

# A preview long enough to know whether it is worth opening.
PREVIEW_LENGTH = 120
TTL_DAYS = 90
DEFAULT_LIMIT = 50

_dynamo = None


def _resource():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo


def _table():
    return _resource().Table(constants.NOTIFICATIONS_TABLE_NAME)


def _preview(text):
    text = (text or "").strip()
    return text[:PREVIEW_LENGTH] + ("…" if len(text) > PREVIEW_LENGTH else "")


def notify(user_ids, kind, actor_id, *, group_id=None, group_name=None,
           quiz_date=None, body=None, comment_id=None):
    """
    Tell these people something happened. Returns how many were told.

    The actor is filtered out here rather than at each call site, because
    "do not tell somebody about their own action" is a property of notifying,
    not of any one caller, and a call site that forgets it is a bug that only
    shows up as somebody being pestered by themselves.
    """
    recipients = [u for u in dict.fromkeys(user_ids) if u and u != actor_id]
    if not recipients:
        return 0

    now = datetime.now(timezone.utc)
    expires = int((now + timedelta(days=TTL_DAYS)).timestamp())

    with _table().batch_writer() as batch:
        for user_id in recipients:
            notification_id = uuid.uuid4().hex[:12]
            batch.put_item(Item={
                "userId": user_id,
                # Newest first on read, and the id keeps two in the same
                # millisecond from colliding.
                "createdAtId": f"{now.isoformat()}#{notification_id}",
                "notificationId": notification_id,
                "kind": kind,
                "actorId": actor_id,
                "groupId": group_id,
                "groupName": group_name,
                "quizDate": quiz_date,
                "commentId": comment_id,
                "preview": _preview(body) if body else None,
                "read": False,
                "createdAt": now.isoformat(),
                "ttl": expires,
            })
    return len(recipients)


def recent(user_id, limit=DEFAULT_LIMIT):
    """Newest first, which is the only order this is ever wanted in."""
    resp = _table().query(
        KeyConditionExpression=Key("userId").eq(user_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    return resp.get("Items", [])


def mark_read(user_id, notification_ids=None):
    """
    Mark some as read, or all of them.

    Reading the list is not the same as having read the things in it, so this
    is explicit rather than a side effect of fetching — otherwise opening the
    page to see what is there marks everything seen whether or not you looked.
    """
    rows = recent(user_id, limit=200)
    wanted = set(notification_ids or [])
    marked = 0
    for row in rows:
        if row.get("read"):
            continue
        if wanted and row.get("notificationId") not in wanted:
            continue
        _table().update_item(
            Key={"userId": user_id, "createdAtId": row["createdAtId"]},
            UpdateExpression="SET #r = :true",
            ExpressionAttributeNames={"#r": "read"},
            ExpressionAttributeValues={":true": True},
        )
        marked += 1
    return marked


def unread_count(rows):
    return sum(1 for r in rows if not r.get("read"))
