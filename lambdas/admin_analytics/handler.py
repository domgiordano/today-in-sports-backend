"""
GET /admin/analytics - the numbers, precomputed.

Reads rollups written by the nightly job. Every figure here is a GetItem, so
the screen costs the same whether ten people play or ten thousand.

Region slicing is not wired up yet and is deliberately absent rather than
faked: it needs a location on the play row, which needs a decision about how
coarse that location should be. Country and state are cheap and defensible;
county needs a paid database, only means anything in one country, and is more
location data than a trivia app can justify keeping against named accounts.
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
    scope = f"group#{group_id}" if group_id else 'global'

    periods = stats_dynamo.list_scope(scope)

    return success_response({
        'scope': scope,
        'periods': {p: periods.get(p) for p in stats_dynamo.VALID_PERIODS},
        'computedAt': (periods.get('all') or {}).get('computedAt'),
        # Named here so the screen can say "nothing yet" rather than showing
        # zeroes that look like a real and very bad result.
        'hasData': bool(periods),
    })
