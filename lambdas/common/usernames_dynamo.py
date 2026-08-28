"""
Claimed @handles.

A handle is unique across the app, which means the claim has to be a conditional
write against a key — not a check-then-write, which two people can pass at the
same time, and not a GSI, which will happily hold two identical values.

The stored key is the handle folded to lower case: "Dom" and "dom" are the same
claim. The casing the owner chose is kept alongside it so their profile can show
what they typed.
"""

import re
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

MIN_LENGTH = 3
MAX_LENGTH = 20

# Letters, digits and underscores. No dots or hyphens: they are the characters
# that make two handles look identical in a sentence, and a game with a
# leaderboard is a game where somebody will try it.
_SHAPE = re.compile(r"^[a-z0-9_]+$")

# Names that would read as the product speaking, or that collide with a path.
# Not exhaustive — it does not need to be, it needs to cover the obvious.
RESERVED = {
    "admin", "administrator", "moderator", "mod", "staff", "team", "official",
    "support", "help", "api", "root", "system", "null", "undefined", "anon",
    "anonymous", "todayinsports", "tis", "about", "settings", "login",
    "logout", "signin", "signup", "account", "profile", "play", "stats",
    "groups", "docs", "me", "you",
}


def normalise(raw):
    """The stored form of a handle: trimmed, stripped of a leading @, folded."""
    return (raw or "").strip().lstrip("@").lower()


def validate(raw):
    """
    Return the normalised handle, or raise ValueError with a reason a person
    can act on.
    """
    handle = normalise(raw)
    if not handle:
        raise ValueError("Pick a username.")
    if len(handle) < MIN_LENGTH:
        raise ValueError(f"Usernames are at least {MIN_LENGTH} characters.")
    if len(handle) > MAX_LENGTH:
        raise ValueError(f"Usernames are at most {MAX_LENGTH} characters.")
    if not _SHAPE.match(handle):
        raise ValueError("Letters, numbers and underscores only.")
    if handle in RESERVED:
        raise ValueError("That one is reserved. Try another.")
    return handle


_dynamo = None


def _resource():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo


def _table():
    return _resource().Table(constants.USERNAMES_TABLE_NAME)


def owner_of(handle):
    """The userId holding this handle, or None."""
    item = _table().get_item(Key={"username": normalise(handle)}).get("Item")
    return (item or {}).get("userId")


def current_for(user_id):
    """The handle this user holds, in the casing they chose, or None."""
    resp = _table().query(
        IndexName=constants.USERNAMES_OWNER_INDEX,
        KeyConditionExpression=Key("userId").eq(user_id),
        Limit=1,
    )
    items = resp.get("Items") or []
    return (items[0].get("display") or items[0]["username"]) if items else None


def handles_for(user_ids):
    """
    {lowercased handle: userId} for the users given.

    Built from the owner index rather than by scanning the table, so it costs
    one query per member instead of a read of every handle on the app. A group
    is capped at fifty, and the caller wants only the people in it.
    """
    out = {}
    for user_id in dict.fromkeys(u for u in user_ids if u):
        resp = _table().query(
            IndexName=constants.USERNAMES_OWNER_INDEX,
            KeyConditionExpression=Key("userId").eq(user_id),
            Limit=1,
        )
        for item in resp.get("Items") or []:
            out[item["username"]] = user_id
    return out


def claim(raw, user_id):
    """
    Take a handle for this user, releasing whatever they held before.

    Raises ValueError if the handle is malformed or already somebody else's.
    Re-claiming your own is a no-op rather than an error, so a form that
    submits an unchanged value does not fail.
    """
    handle = validate(raw)
    display = (raw or "").strip().lstrip("@")

    existing_owner = owner_of(handle)
    if existing_owner == user_id:
        # Same person, possibly changing only the casing they display.
        _table().update_item(
            Key={"username": handle},
            UpdateExpression="SET display = :d",
            ExpressionAttributeValues={":d": display},
        )
        return handle
    if existing_owner:
        raise ValueError("That username is taken.")

    try:
        _table().put_item(
            Item={
                "username": handle,
                "display": display,
                "userId": user_id,
                "claimedAt": datetime.now(timezone.utc).isoformat(),
            },
            # The whole point. Two people submitting the same handle at the
            # same moment both pass the read above; only one passes this.
            ConditionExpression="attribute_not_exists(username)",
        )
    except Exception as exc:  # noqa: BLE001 - conditional failure is the case
        if "ConditionalCheckFailed" in str(type(exc).__name__) or \
                "ConditionalCheckFailed" in str(exc):
            raise ValueError("That username is taken.")
        raise

    # Released only once the new one is held, so a failure midway leaves the
    # user with their old handle rather than with none.
    previous = _previous_handles(user_id, keep=handle)
    for old in previous:
        _table().delete_item(Key={"username": old})

    return handle


def _previous_handles(user_id, keep):
    resp = _table().query(
        IndexName=constants.USERNAMES_OWNER_INDEX,
        KeyConditionExpression=Key("userId").eq(user_id),
    )
    return [i["username"] for i in resp.get("Items", []) if i["username"] != keep]


def release_all(user_id):
    """Give up every handle this user holds. Used when an account is deleted."""
    for handle in _previous_handles(user_id, keep=None):
        _table().delete_item(Key={"username": handle})
