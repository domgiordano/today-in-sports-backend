"""
Friends.

Mutual, not a follow. You are comparing daily scores with somebody, and a
one-way follow would let anyone watch your results without you agreeing to it —
a different product, and a worse one for an app whose groups are private by
design.

Stored as two rows per relationship, one from each side, always written
together. That is deliberate: the question asked constantly is "who are my
friends", and one query answers it from either side. A single canonical row
keyed on the sorted pair would halve the storage and double the work on every
read.

The two rows carry mirrored status:

    requester   pending_out   "I asked them"
    recipient   pending_in    "they asked me"

and both become `accepted` together. Nothing is ever left half-accepted,
because the accept writes both rows in one transaction.
"""

from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

PENDING_OUT = "pending_out"
PENDING_IN = "pending_in"
ACCEPTED = "accepted"

# Enough for a daily quiz played with people you know. A cap here is not
# capacity — it is a bound on how much a single account can fan out.
MAX_FRIENDS = 150

_dynamo = None


def _resource():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo


def _table():
    return _resource().Table(constants.FRIENDS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _row(user_id, friend_id, status, now):
    return {
        "userId": user_id,
        "friendId": friend_id,
        "status": status,
        "updatedAt": now,
    }


def edge(user_id, friend_id):
    """This user's own view of one relationship, or None."""
    resp = _table().get_item(Key={"userId": user_id, "friendId": friend_id})
    return resp.get("Item")


def for_user(user_id):
    """Every relationship this user has, in any state."""
    resp = _table().query(KeyConditionExpression=Key("userId").eq(user_id))
    return resp.get("Items", [])


def counts(user_id):
    rows = for_user(user_id)
    return {
        "accepted": sum(1 for r in rows if r.get("status") == ACCEPTED),
        "incoming": sum(1 for r in rows if r.get("status") == PENDING_IN),
        "outgoing": sum(1 for r in rows if r.get("status") == PENDING_OUT),
    }


def request(user_id, target_id):
    """
    Ask somebody to be friends. Returns the resulting status.

    Re-requesting somebody who already asked you is an accept rather than a
    second request. Two people reaching for each other at the same time is the
    obvious way to end up with a pair of pending rows and nobody friends, and
    the fix costs one branch.
    """
    if user_id == target_id:
        raise ValueError("you cannot add yourself")

    existing = edge(user_id, target_id)
    if existing:
        status = existing.get("status")
        if status == ACCEPTED:
            return ACCEPTED
        if status == PENDING_IN:
            accept(user_id, target_id)
            return ACCEPTED
        return PENDING_OUT

    if counts(user_id)["accepted"] >= MAX_FRIENDS:
        raise ValueError(f"you can have at most {MAX_FRIENDS} friends")

    now = _now()
    _resource().meta.client.transact_write_items(TransactItems=[
        {"Put": {"TableName": constants.FRIENDS_TABLE_NAME,
                 "Item": _ddb(_row(user_id, target_id, PENDING_OUT, now))}},
        {"Put": {"TableName": constants.FRIENDS_TABLE_NAME,
                 "Item": _ddb(_row(target_id, user_id, PENDING_IN, now))}},
    ])
    return PENDING_OUT


def accept(user_id, requester_id):
    """
    Accept a request. Both rows flip together or neither does.

    Guarded on the row actually being an incoming request, so a replayed or
    forged accept cannot invent a friendship that nobody asked for.
    """
    existing = edge(user_id, requester_id)
    if not existing or existing.get("status") != PENDING_IN:
        raise ValueError("no request from that player to accept")

    now = _now()
    _resource().meta.client.transact_write_items(TransactItems=[
        {"Put": {"TableName": constants.FRIENDS_TABLE_NAME,
                 "Item": _ddb(_row(user_id, requester_id, ACCEPTED, now))}},
        {"Put": {"TableName": constants.FRIENDS_TABLE_NAME,
                 "Item": _ddb(_row(requester_id, user_id, ACCEPTED, now))}},
    ])


def remove(user_id, other_id):
    """
    Decline a request, withdraw one, or unfriend. All three are the same
    write: the relationship stops existing from both sides.

    Removing from one side only would leave the other person still seeing a
    friend who cannot see them, which is worse than either state.
    """
    _resource().meta.client.transact_write_items(TransactItems=[
        {"Delete": {"TableName": constants.FRIENDS_TABLE_NAME,
                    "Key": {"userId": {"S": user_id}, "friendId": {"S": other_id}}}},
        {"Delete": {"TableName": constants.FRIENDS_TABLE_NAME,
                    "Key": {"userId": {"S": other_id}, "friendId": {"S": user_id}}}},
    ])


def _ddb(item):
    """The low-level client needs typed attributes; everything here is a string."""
    return {k: {"S": v} for k, v in item.items()}
