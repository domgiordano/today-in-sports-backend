"""
Friend groups.

Joining is by invite code and never by a searchable directory. A public list of
small private groups is a harassment surface with no upside for a trivia game,
so there is deliberately no endpoint that answers "what groups exist".

The code is short enough to read down a phone line and regenerable, so a group
that leaks one can close it without rebuilding itself.
"""

import secrets
import string
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

# No vowels, so the generator cannot produce a real word by accident, and no
# characters that are read wrong out loud: 0/O, 1/I/L, 5/S, 8/B.
CODE_ALPHABET = "CDFGHJKMNPQRTVWXY234679"
CODE_LENGTH = 6

# Big enough that a group is a real social unit and small enough that the
# leaderboard fits on a phone without paging.
MAX_MEMBERS = 50

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.GROUPS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat()


def generate_code():
    """A short, speakable invite code."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def get_group(group_id):
    return _table().get_item(Key={"groupId": group_id}).get("Item")


def by_invite_code(code):
    """Resolve a code to its group, or None."""
    if not code:
        return None
    resp = _table().query(
        IndexName=constants.GROUPS_INVITE_INDEX,
        KeyConditionExpression=Key("inviteCode").eq(code.strip().upper()),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def create_group(name, owner_id):
    """
    Create a group with its owner as the first member.

    The invite code is checked for a collision rather than assumed unique. Six
    characters from a 24-symbol alphabet is around 190 million combinations, so
    a clash is unlikely - but "unlikely" and "handled" are different things, and
    a silent collision would drop two groups' members into one.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("a group needs a name")

    for _ in range(5):
        code = generate_code()
        if not by_invite_code(code):
            break
    else:
        raise RuntimeError("could not find a free invite code")

    item = {
        "groupId": str(uuid.uuid4()),
        "name": name[:60],
        "ownerId": owner_id,
        "inviteCode": code,
        "memberIds": {owner_id},
        "createdAt": _now(),
    }
    _table().put_item(Item=item)
    log.info(f"group {item['groupId']} created by {owner_id}")
    return item


def join_group(code, user_id):
    """Add a member by invite code. Idempotent; returns the group."""
    group = by_invite_code(code)
    if not group:
        raise ValueError("no group with that code")

    members = set(group.get("memberIds") or set())
    if user_id in members:
        return group
    if len(members) >= MAX_MEMBERS:
        raise ValueError(f"this group is full ({MAX_MEMBERS} members)")

    resp = _table().update_item(
        Key={"groupId": group["groupId"]},
        UpdateExpression="ADD memberIds :m",
        ExpressionAttributeValues={":m": {user_id}},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def leave_group(group_id, user_id):
    """
    Remove a member.

    The owner leaving does not delete the group - the others are still playing
    in it. Ownership simply becomes vestigial, which is a smaller problem than
    a group disappearing under the people using it.
    """
    resp = _table().update_item(
        Key={"groupId": group_id},
        UpdateExpression="DELETE memberIds :m",
        ExpressionAttributeValues={":m": {user_id}},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def regenerate_code(group_id, user_id):
    """Owner-only: retire a leaked code without rebuilding the group."""
    group = get_group(group_id)
    if not group:
        raise ValueError("no such group")
    if group.get("ownerId") != user_id:
        raise PermissionError("only the owner can change the invite code")

    code = generate_code()
    resp = _table().update_item(
        Key={"groupId": group_id},
        UpdateExpression="SET inviteCode = :c",
        ExpressionAttributeValues={":c": code},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def groups_for(group_ids):
    """Hydrate a user's group ids. Small lists; a get each is honest."""
    out = []
    for group_id in sorted(group_ids or ()):
        group = get_group(group_id)
        if group:
            out.append(group)
    return out


def public_view(group, include_code=False):
    """
    What a member may see.

    The invite code is only returned to someone already inside the group -
    otherwise the code is discoverable by anyone who can name a group id, which
    would make it not a secret.
    """
    view = {
        "groupId": group.get("groupId"),
        "name": group.get("name"),
        "ownerId": group.get("ownerId"),
        "memberCount": len(group.get("memberIds") or set()),
        "createdAt": group.get("createdAt"),
    }
    if include_code:
        view["inviteCode"] = group.get("inviteCode")
    return view
