"""
Nightly: precompute the analytics numbers.

Scanning the plays table to answer "what was the average score this month" is
fine at ten players and falls over at ten thousand, without anybody having
changed anything. So it is computed once here and read back by key.

Scopes rolled up: global, then one per group, so a group screen is a GetItem
rather than a filtered scan of everybody's play.
"""

from datetime import datetime, timedelta, timezone

import boto3

from lambdas.common import (
    constants,
    groups_dynamo,
    stats_dynamo,
    users_dynamo,
)
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'cron_rollup_stats'

WINDOWS = {'week': 7, 'month': 30, 'all': None}

_dynamo = None


def _plays_table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource('dynamodb')
    return _dynamo.Table(constants.PLAYS_TABLE_NAME)


def _all_sessions():
    """
    Every completed session.

    A full scan, deliberately and only here: this runs once a night, off the
    request path, and it is what buys every read afterwards being a GetItem.
    """
    out, last_key = [], None
    while True:
        kwargs = {}
        if last_key:
            kwargs['ExclusiveStartKey'] = last_key
        resp = _plays_table().scan(**kwargs)
        out.extend(r for r in resp.get('Items', []) if r.get('completedAt'))
        last_key = resp.get('LastEvaluatedKey')
        if not last_key:
            break
    return out


def _within(sessions, days):
    if days is None:
        return sessions
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    return [s for s in sessions if (s.get('quizDate') or '') >= cutoff]


@handle_errors(HANDLER)
def handler(event, context):
    sessions = _all_sessions()
    log.info(f'rolling up {len(sessions)} completed sessions')

    written = 0
    for period, days in WINDOWS.items():
        window = _within(sessions, days)
        stats_dynamo.put_rollup('global', period, stats_dynamo.summarise(window))
        written += 1

    # One scope per group. Groups are small and few; if that ever stops being
    # true this is the loop to bound.
    groups = _all_groups()
    for group in groups:
        members = set(group.get('memberIds') or set())
        if not members:
            continue
        scope = f"group#{group['groupId']}"
        theirs = [s for s in sessions if s.get('identity') in members]
        for period, days in WINDOWS.items():
            stats_dynamo.put_rollup(
                scope, period, stats_dynamo.summarise(_within(theirs, days)))
            written += 1

    # One scope per country that anybody has declared. Self-declared and
    # coarse - see users_dynamo.set_region for why it is not derived from an
    # IP address.
    by_country = {}
    for user in users_dynamo.list_users(500):
        country = user.get('country')
        if country:
            by_country.setdefault(country, set()).add(user['userId'])

    for country, ids in by_country.items():
        theirs = [s for s in sessions if s.get('identity') in ids]
        for period, days in WINDOWS.items():
            stats_dynamo.put_rollup(
                f'region#{country}', period,
                stats_dynamo.summarise(_within(theirs, days)))
            written += 1

    scopes = len(groups) + len(by_country) + 1
    log.info(f'wrote {written} rollups across {scopes} scopes')
    return success_response({
        'sessions': len(sessions),
        'scopes': scopes,
        'countries': sorted(by_country),
        'rollups': written,
    })


def _all_groups():
    table = boto3.resource('dynamodb').Table(constants.GROUPS_TABLE_NAME)
    out, last_key = [], None
    while True:
        kwargs = {}
        if last_key:
            kwargs['ExclusiveStartKey'] = last_key
        resp = table.scan(**kwargs)
        out.extend(resp.get('Items', []))
        last_key = resp.get('LastEvaluatedKey')
        if not last_key:
            break
    return out
