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
from lambdas.common.notability import f1 as f1_nb
from lambdas.common.notability import mlb as mlb_nb
from lambdas.common.notability import nba as nba_nb
from lambdas.common.notability import nfl as nfl_nb
from lambdas.common.notability import nhl as nhl_nb
from lambdas.common.notability import soccer as soccer_nb
from lambdas.common.sources import balldontlie as nba_src
from lambdas.common.sources import f1db as f1_src
from lambdas.common.sources import football_json as soccer_src
from lambdas.common.sources import mlb as mlb_src
from lambdas.common.sources import nba_franchises
from lambdas.common.sources import nflverse as nfl_src
from lambdas.common.sources import nhl as nhl_src
from lambdas.common.templates import mlb_templates as mlb_tpl
from lambdas.common.templates import winter_templates as winter_tpl
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'cron_ingest_recent'

DEFAULT_LOOKBACK_DAYS = 8  # a day of overlap, so a failed run self-heals

# Lambda's writable scratch, for the sources that are files rather than
# endpoints. Cold storage between invocations is not assumed - a cold start
# simply re-downloads.
CACHE_DIR = "/tmp/tis-cache"

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


def _season_of(day, first_month):
    """
    The season a date belongs to, for the sports whose seasons cross new year.

    A January NBA game belongs to the season that began the previous October,
    and asking for the wrong one returns a season that has not started.
    """
    d = date.fromisoformat(day)
    return d.year if d.month >= first_month else d.year - 1


def _recent(games, days):
    wanted = set(days)
    return [g for g in games if g.get("gameDate") in wanted]


def _ingest_nba(days):
    """
    Basketball. The season file is fetched whole and filtered, because
    balldontlie pages by season rather than by date.
    """
    if not days:
        return [], []
    season = _season_of(max(days), first_month=10)
    games = nba_src.fetch_season(season)
    final = [g for g in _recent(games, days) if nba_src.is_final(g)]
    return final, nba_nb.run(final)


def _ingest_soccer(days):
    """
    Soccer. openfootball publishes a file per league per season and updates it
    as the season runs, so recency comes from refetching rather than from a
    date parameter.
    """
    if not days:
        return [], []
    start = _season_of(max(days), first_month=7)
    # The export names most seasons "2024-25" but the current one plainly.
    for label in (f"{start}-{str(start + 1)[2:]}", str(start)):
        try:
            games = soccer_src.fetch_season(label, CACHE_DIR)
        except Exception as exc:                       # noqa: BLE001
            log.warning(f"soccer {label}: {exc}")
            continue
        if games:
            final = _recent(games, days)
            return final, soccer_nb.run(final)
    return [], []


def _ingest_f1(days):
    """Formula One. One dump covers every season; filter to the window."""
    if not days:
        return [], []
    f1_src.download(CACHE_DIR)
    races = f1_src.load_races(CACHE_DIR)
    recent = _recent(races, days)
    return recent, f1_nb.run(recent)


def _ingest_nfl(days):
    """American football. One CSV covers every season since 1999."""
    if not days:
        return [], []
    games = nfl_src.load_games(CACHE_DIR)
    recent = _recent(games, days)
    return recent, nfl_nb.run(recent)


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

    # Each sport is isolated. A source that has moved, rate-limited or gone
    # down should cost its own sport's week, never the other five - and this
    # job previously covered only two sports, so a quiet failure in either was
    # indistinguishable from a quiet week.
    per_sport, games, events = {}, {}, {}
    for sport, ingest in (("mlb", _ingest_mlb), ("nhl", _ingest_nhl),
                          ("nba", _ingest_nba), ("soccer", _ingest_soccer),
                          ("nfl", _ingest_nfl), ("f1", _ingest_f1)):
        try:
            games[sport], events[sport] = ingest(days)
        except Exception as exc:                       # noqa: BLE001
            log.warning(f"{sport}: {type(exc).__name__}: {exc}")
            games[sport], events[sport] = [], []
            per_sport[sport] = {"games": 0, "events": 0,
                                "error": f"{type(exc).__name__}: {exc}"[:200]}
            continue
        per_sport[sport] = {"games": len(games[sport]),
                            "events": len(events[sport])}

    games_scanned = sum(len(g) for g in games.values())
    mlb_games, mlb_events = games["mlb"], events["mlb"]
    winter_events = [e for sport in ("nhl", "nba", "soccer", "nfl", "f1")
                     for e in events[sport]]
    all_events = mlb_events + winter_events

    # Questions need their sport's own templates; reason codes are not unique
    # across sports, so the two template sets are kept apart deliberately.
    # winter_templates covers everything that is not baseball.
    questions = []
    for e in mlb_events:
        questions.extend(mlb_tpl.generate([e], mlb_games))
    questions.extend(winter_tpl.generate(
        winter_events,
        winter_tpl.build_context(winter_events,
                                 nba_franchises.load(CACHE_DIR))))

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
        'gamesScanned': games_scanned,
        'bySport': per_sport,
        'eventsDetected': len(all_events),
        'questionsDrafted': len(valid),
        'questionsDropped': dropped,
        'finishedAt': datetime.now(timezone.utc).isoformat(),
    }))

    log.info(f'{len(all_events)} events, {len(valid)} drafts, {dropped} dropped')
    return success_response({
        'daysScanned': len(days),
        'gamesScanned': games_scanned,
        'bySport': per_sport,
        'eventsDetected': len(all_events),
        'questionsDrafted': len(valid),
        'questionsDropped': dropped,
    })
