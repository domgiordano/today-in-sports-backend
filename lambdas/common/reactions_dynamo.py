"""
Emoji left on somebody's round.

Keyed by the round it is about and by who left it, so one person reacts once to
a given round and reacting again replaces rather than stacks. The alternative
is a count that measures how many times somebody tapped rather than how many
people thought a score was worth remarking on.

Signed-in players only. An anonymous identity is a device id, which anyone can
reset, so allowing anonymous reactions would make the count a measure of how
many times a person cleared their browser.
"""

from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

# A closed set, deliberately.
#
# Free-form emoji on a leaderboard is a moderation surface — there is no
# shortage of unpleasant things to leave against somebody's name — and a fixed
# palette makes the feature legible at a glance instead of being a text field
# in disguise.
ALLOWED = ("👏", "🔥", "😂", "😱", "🧠", "💀")

# Reactions expire with the round they are about. A reaction to a quiz nobody
# can still reach is not worth keeping, and it means this table never needs
# sweeping.
TTL_DAYS = 90

_dynamo = None


def _resource():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo


def _table():
    return _resource().Table(constants.REACTIONS_TABLE_NAME)


def set_reaction(play_id, quiz_date, reactor_id, emoji):
    """
    Leave, change or clear a reaction. Returns the emoji now in place, or None.

    Passing an emoji already in place clears it, so the same tap is both the
    on and the off switch — which is what a player expects from a button that
    shows its own state.
    """
    if emoji is not None and emoji not in ALLOWED:
        raise ValueError(f"{emoji!r} is not one of the available reactions")

    key = {"playId": play_id, "reactorId": reactor_id}
    current = _table().get_item(Key=key).get("Item") or {}

    if emoji is None or current.get("emoji") == emoji:
        _table().delete_item(Key=key)
        return None

    expires = datetime.now(timezone.utc) + timedelta(days=TTL_DAYS)
    _table().put_item(Item={
        **key,
        "emoji": emoji,
        "quizDate": quiz_date,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "ttl": int(expires.timestamp()),
    })
    return emoji


def for_day(quiz_date):
    """
    Every reaction left on a given day, as {playId: {emoji: count}}.

    One query rather than one per row: a board is up to fifty rounds, and the
    day index exists so this does not become fifty reads to render one page.
    """
    counts, mine = {}, {}
    kwargs = {
        "IndexName": constants.REACTIONS_DAY_INDEX,
        "KeyConditionExpression": Key("quizDate").eq(quiz_date),
    }
    while True:
        resp = _table().query(**kwargs)
        for item in resp.get("Items", []):
            tally = counts.setdefault(item["playId"], {})
            tally[item["emoji"]] = tally.get(item["emoji"], 0) + 1
            mine.setdefault(item["reactorId"], {})[item["playId"]] = item["emoji"]
        if "LastEvaluatedKey" not in resp:
            return counts, mine
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
