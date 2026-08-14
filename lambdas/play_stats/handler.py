"""
GET /play/stats - the public numbers.

The same precomputed rollups the admin screen reads, minus anything that
identifies a player. Every figure is a keyed read, so this costs the same
whether ten people play or ten thousand.

What is deliberately not here:

  * No group scope. A group is private by design - you join by being handed a
    code, and there is no way to list them - so exposing a group's numbers on
    an unauthenticated endpoint would undo that. Group slices stay on the
    authenticated path.
  * No player names, ids or device ids. Aggregates only.
  * Regions are only offered once enough people in one have played. A country
    with two players is a pair of individuals, not a demographic, and its
    "average score" is one person's bad morning.
"""

from lambdas.common import stats_dynamo
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'play_stats'

# Below this many distinct players, a region is not published.
MIN_REGION_PLAYERS = 5

PUBLIC_FIELDS = (
    'rounds', 'players', 'avgPoints', 'avgCorrect',
    'perfectRounds', 'avgSeconds', 'bestPoints', 'bySport',
)


def _public(rollup):
    if not rollup:
        return None
    return {field: rollup.get(field) for field in PUBLIC_FIELDS}


def _trend(periods):
    """The per-day series, oldest first, as written by the nightly job."""
    days = []
    for key, row in periods.items():
        if not key.startswith('day#'):
            continue
        days.append({
            'date': key[4:],
            'rounds': int(row.get('rounds') or 0),
            'players': int(row.get('players') or 0),
            'avgPoints': int(row.get('avgPoints') or 0),
        })
    return sorted(days, key=lambda d: d['date'])


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    country = (params.get('country') or '').strip().upper()

    scope = f'region#{country}' if country else 'global'
    periods = stats_dynamo.list_scope(scope)

    # A region that nobody has played is not an error, but it is not a slice
    # worth showing either — fall back rather than return a page of zeroes.
    summary = _public(periods.get('all'))
    if country and (not summary or (summary.get('players') or 0) < MIN_REGION_PLAYERS):
        log.info(f'region {country} below the publishing floor; serving global')
        scope = 'global'
        periods = stats_dynamo.list_scope(scope)
        summary = _public(periods.get('all'))

    return success_response({
        'scope': scope,
        'all': summary,
        'week': _public(periods.get('week')),
        'month': _public(periods.get('month')),
        'trend': _trend(periods),
        'computedAt': (periods.get('all') or {}).get('computedAt'),
        # Named, so the page can be absent rather than show zeroes that read as
        # a real and very bad result.
        'hasData': bool(summary and (summary.get('rounds') or 0) > 0),
    })
