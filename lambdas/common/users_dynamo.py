"""
The user record.

Cognito holds credentials and nothing else, so this is where a person's
history lives: how many quizzes they have played, their streak, their badges,
which groups they are in.

Written lazily, on the first authenticated request that needs it, rather than
by a Cognito post-confirmation trigger. A trigger is a second deploy target and
carries a failure mode where sign-up succeeds and the profile silently does
not - which surfaces much later as a user who exists but has no history.

Streaks are stored, not recomputed. Recomputing means reading a play history
that only grows, on every request, forever.
"""

from datetime import date, datetime, timedelta, timezone

import boto3

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.USERS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc)


def get_user(user_id):
    return _table().get_item(Key={"userId": user_id}).get("Item")


def ensure_user(user_id, email=None, display_name=None):
    """
    Create the record if it is missing, and stamp last seen either way.

    Idempotent by design: every authenticated request may call this, and the
    common case is an update of one attribute rather than a write.
    """
    now = _now().isoformat()

    expression = (
        "SET lastSeenAt = :now, "
        "createdAt = if_not_exists(createdAt, :now), "
        "playCount = if_not_exists(playCount, :zero), "
        "currentStreak = if_not_exists(currentStreak, :zero), "
        "longestStreak = if_not_exists(longestStreak, :zero)"
    )
    values = {":now": now, ":zero": 0}

    if email:
        expression += ", email = :email"
        values[":email"] = email
    if display_name:
        expression += ", displayName = if_not_exists(displayName, :dn)"
        values[":dn"] = display_name

    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression=expression,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def next_streak(last_played, today, current):
    """
    The streak after playing on `today`.

    Three cases, and the middle one is the whole mechanic:

      * played already today - unchanged, because a second session is not a
        second day
      * played yesterday - one longer
      * anything else, including a first ever play - back to one

    A missed day resets. That is the mechanic working, and it is also the most
    common reason people abandon a daily game, so it is the number to revisit
    with real data rather than to soften now on a guess.
    """
    current = int(current or 0)
    if not last_played:
        return 1
    if last_played == today:
        return max(current, 1)

    try:
        previous = date.fromisoformat(last_played)
        current_day = date.fromisoformat(today)
    except (TypeError, ValueError):
        return 1

    if current_day - previous == timedelta(days=1):
        return current + 1
    return 1


def record_play(user_id, quiz_date, points, correct_count, badge_ids):
    """
    Fold a finished round into the user's history.

    Returns the updated record and the badges that were genuinely new, so the
    client can show the moment one is earned rather than quietly listing it in
    a profile the player may never open.
    """
    user = get_user(user_id) or {}

    last_played = user.get("lastPlayedDate")
    already_today = last_played == quiz_date

    streak = next_streak(last_played, quiz_date, user.get("currentStreak"))
    longest = max(int(user.get("longestStreak") or 0), streak)

    held = set(user.get("badges") or [])
    fresh = [b for b in badge_ids if b not in held]

    expression = (
        "SET currentStreak = :streak, longestStreak = :longest, "
        "lastPlayedDate = :qd, lastSeenAt = :now, "
        "badges = :badges, "
        "totalPoints = if_not_exists(totalPoints, :zero) + :points, "
        "totalCorrect = if_not_exists(totalCorrect, :zero) + :correct"
    )
    values = {
        ":streak": streak,
        ":longest": longest,
        ":qd": quiz_date,
        ":now": _now().isoformat(),
        ":badges": sorted(held | set(badge_ids)),
        ":zero": 0,
        ":points": int(points or 0),
        ":correct": int(correct_count or 0),
    }

    # A replayed day must not inflate the lifetime count. The score and the
    # correct tally are additive by design, but "quizzes played" is a count of
    # days and a second session on one day is still one day.
    if not already_today:
        expression += ", playCount = if_not_exists(playCount, :zero) + :one"
        values[":one"] = 1

    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression=expression,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    updated = resp.get("Attributes") or {}
    log.info(f"{user_id} played {quiz_date}: streak {streak}, "
             f"{len(fresh)} new badge(s)")
    return updated, fresh


# ISO 3166-1 alpha-2, and a free-text subdivision. Self-declared rather than
# derived from an IP address: API Gateway hands over no location, so the
# alternatives are a geolocation database in the layer or putting the API
# behind CloudFront - and neither is worth it when people identify with a
# country rather than with their VPN exit node.
MAX_REGION_LEN = 60


def set_region(user_id, country, subdivision=None):
    """
    Record where a player says they are.

    Stored coarsely and deliberately: a country and, optionally, a state or
    region. No city, no county, no coordinates. This is for a leaderboard
    filter, and anything finer would be more location data than a trivia game
    can justify keeping against a named account.
    """
    country = (country or "").strip().upper()[:2]
    if not country:
        raise ValueError("a country is required")

    expression = "SET country = :c"
    values = {":c": country}

    subdivision = (subdivision or "").strip()[:MAX_REGION_LEN]
    if subdivision:
        expression += ", subdivision = :s"
        values[":s"] = subdivision
    else:
        expression += " REMOVE subdivision"

    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression=expression,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def clear_region(user_id):
    """
    Take a region back off.

    Somebody who told us where they are should be able to stop telling us
    without deleting their account.
    """
    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="REMOVE country, subdivision",
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def set_display_name(user_id, name):
    resp = _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="SET displayName = :n",
        ExpressionAttributeValues={":n": name},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def add_group(user_id, group_id):
    _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="ADD groupIds :g",
        ExpressionAttributeValues={":g": {group_id}},
    )


def remove_group(user_id, group_id):
    _table().update_item(
        Key={"userId": user_id},
        UpdateExpression="DELETE groupIds :g",
        ExpressionAttributeValues={":g": {group_id}},
    )


def list_users(limit=200):
    """Every user, for the admin panel. Small table; a scan is honest here."""
    out, last_key = [], None
    while True:
        kwargs = {"Limit": min(int(limit), 500)}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _table().scan(**kwargs)
        out.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key or len(out) >= limit:
            break
    return out[:limit]
