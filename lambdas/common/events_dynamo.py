"""
Read access to stored events, for building distractor pools from history.
"""

from __future__ import annotations

from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.EVENTS_TABLE_NAME)


def _plain(o):
    if isinstance(o, list):
        return [_plain(v) for v in o]
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    return o


def list_by_sport_year(sport: str, year: int) -> list[dict]:
    cond = Key("sport").eq(sport) & Key("year").eq(int(year))
    out, kwargs = [], {"IndexName": constants.EVENTS_SPORT_INDEX, "KeyConditionExpression": cond}
    while True:
        resp = _table().query(**kwargs)
        out.extend(_plain(i) for i in resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return out
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def same_seasons(events: list[dict]) -> list[dict]:
    """
    Stored events from the sports and years `events` belong to.

    A week of recent games names too few clubs to draw three distractors from.
    The previous calendar year comes too, because soccer and basketball seasons
    cross new year.
    """
    keys = {(e["sport"], y) for e in events for y in (e["year"], e["year"] - 1)}
    return [i for sport, year in sorted(keys) for i in list_by_sport_year(sport, year)]
