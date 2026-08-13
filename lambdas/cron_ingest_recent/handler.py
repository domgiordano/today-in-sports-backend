"""
Weekly: ingest the last N days from the live APIs and turn them into drafts.

**Only tier 1 needs this.** Everything older than a year is immutable history,
already backfilled, and never re-fetched — 11,000+ events covering 1871 onwards
do not change. What decays is the "last 12 months" bucket, continuously, and
this is what keeps it alive.

It deliberately uses the *live* APIs rather than the sources of record. The
dumps this project prefers — Retrosheet, f1db, nflverse — publish after a season
ends, so they cannot serve a game played on Tuesday. The annual reconciliation
cron replaces this API-derived data with the authoritative version once the
season files drop.

Output is always `draft`. Nothing generated here reaches a player without a
human approving it.
"""

import os
from datetime import date, datetime, timedelta, timezone

import boto3

from lambdas.common import constants
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.notability import mlb as mlb_nb
from lambdas.common.notability import nhl as nhl_nb
from lambdas.common.sources import mlb as mlb_src
from lambdas.common.sources import nhl as nhl_src
from lambdas.common.templates import mlb_templates as mlb_tpl
from lambdas.common.templates import winter_templates as winter_tpl
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'cron_ingest_recent'

DEFAULT_LOOKBACK_DAYS = 8  # a day of overlap, so a failed run self-heals

_dynamo = None


def _table(name):
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource('dynamodb')
    return _dynamo.Table(name)


def _clean(obj):
    from decimal import Decimal
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if v != '' and v is not None}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def _dates(lookback):
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=n)).isoformat() for n in range(1, lookback + 1)]


def _ingest_mlb(days):
    games, events = [], []
    for d in days:
        try:
            day_games, _ = mlb_src.fetch_date(d)
        except Exception as exc:
            log.warning(f'mlb {d}: {exc}')
            continue
        final = [g for g in day_games if mlb_src.is_final(g)]
        games.extend(final)
        events.extend(mlb_nb.run(final))
    return games, events


def _ingest_nhl(days):
    if not days:
        return [], []
    # One request covers a week, so fetch from the oldest date requested.
    try:
        games, _ = nhl_src.fetch_week(min(days))
    except Exception as exc:
        log.warning(f'nhl week {min(days)}: {exc}')
        return [], []
    wanted = set(days)
    final = [g for g in games
             if g.get('gameDate') in wanted and nhl_src.is_final(g)]
    return final, nhl_nb.run(final)


@handle_errors(HANDLER)
def handler(event, context):
    lookback = int((event or {}).get('lookbackDays', DEFAULT_LOOKBACK_DAYS))
    days = _dates(lookback)
    log.info(f'ingesting {len(days)} days: {days[-1]}..{days[0]}')

    mlb_games, mlb_events = _ingest_mlb(days)
    nhl_games, nhl_events = _ingest_nhl(days)

    all_events = mlb_events + nhl_events

    # Questions need their sport's own templates; reason codes are not unique
    # across sports, so the two template sets are kept apart deliberately.
    questions = []
    for e in mlb_events:
        questions.extend(mlb_tpl.generate([e], mlb_games))
    questions.extend(winter_tpl.generate(nhl_events))

    valid = [q for q in questions if not mlb_tpl.validate(q)]
    dropped = len(questions) - len(valid)

    events_table = _table(constants.EVENTS_TABLE_NAME)
    with events_table.batch_writer() as batch:
        for e in all_events:
            batch.put_item(Item=_clean({
                **e,
                'yearEventId': f"{e['year']}#{e['gameId']}",
            }))

    questions_table = _table(constants.QUESTIONS_TABLE_NAME)
    with questions_table.batch_writer(overwrite_by_pkeys=['questionId']) as batch:
        for q in valid:
            batch.put_item(Item=_clean({
                **q,
                'sportTier': f"{q['sport']}#{q['tier']}",
                'status': 'draft',
            }))

    _table(constants.SOURCE_RUNS_TABLE_NAME).put_item(Item=_clean({
        'runId': f"recent-{date.today().isoformat()}",
        'source': 'live-apis',
        'status': 'complete',
        'daysScanned': len(days),
        'gamesScanned': len(mlb_games) + len(nhl_games),
        'eventsDetected': len(all_events),
        'questionsDrafted': len(valid),
        'questionsDropped': dropped,
        'finishedAt': datetime.now(timezone.utc).isoformat(),
    }))

    log.info(f'{len(all_events)} events, {len(valid)} drafts, {dropped} dropped')
    return success_response({
        'daysScanned': len(days),
        'gamesScanned': len(mlb_games) + len(nhl_games),
        'eventsDetected': len(all_events),
        'questionsDrafted': len(valid),
        'questionsDropped': dropped,
    })
