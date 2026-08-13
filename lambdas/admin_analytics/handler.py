"""
GET /admin/analytics - the numbers, precomputed.

Reads rollups written by the nightly job. Every figure here is a GetItem, so
the screen costs the same whether ten people play or ten thousand.

Sliceable by group or by country. Region is self-declared on the profile and
stored coarsely - a country, optionally a state - because that is enough to
filter a leaderboard and anything finer would be more location data than a
trivia game can justify holding against a named account.
"""

from lambdas.common import groups_dynamo, stats_dynamo
from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'admin_analytics'


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    group_id = params.get('groupId')
    country = (params.get('country') or '').strip().upper()

    if group_id:
        scope = f"group#{group_id}"
    elif country:
        scope = f"region#{country}"
    else:
        scope = 'global'

    periods = stats_dynamo.list_scope(scope)

    return success_response({
        'scope': scope,
        'periods': {p: periods.get(p) for p in stats_dynamo.VALID_PERIODS},
        'computedAt': (periods.get('all') or {}).get('computedAt'),
        # Named here so the screen can say "nothing yet" rather than showing
        # zeroes that look like a real and very bad result.
        'hasData': bool(periods),
    })
