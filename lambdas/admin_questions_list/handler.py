"""
GET /admin/questions - browse the question bank.

Filters: status (default draft), mmdd, sport, tier, limit.
"""

from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response
from lambdas.common import questions_dynamo

log = get_logger(__file__)

HANDLER = 'admin_questions_list'


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    status = params.get('status', 'draft')
    mmdd = params.get('mmdd')
    sport = params.get('sport')
    tier = params.get('tier')
    limit = int(params.get('limit', 100))

    if sport or tier:
        items = questions_dynamo.list_bank(status, sport, tier, limit)
        next_key = None
    else:
        items, next_key = questions_dynamo.list_by_status(status, mmdd, limit)

    log.info(f"listed {len(items)} questions status={status} mmdd={mmdd}")
    return success_response({
        'questions': items,
        'count': len(items),
        'nextKey': next_key,
    })
