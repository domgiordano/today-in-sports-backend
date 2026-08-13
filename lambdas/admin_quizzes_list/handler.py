"""
GET /admin/quizzes - the schedule view.

Params: startDate, endDate, or status.
"""

from datetime import date, timedelta

from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response
from lambdas.common import constants, quizzes_dynamo

log = get_logger(__file__)

HANDLER = 'admin_quizzes_list'


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    if params.get('status'):
        items = quizzes_dynamo.list_by_status(params['status'])
    else:
        start = params.get('startDate') or date.today().isoformat()
        end = params.get('endDate') or (
            date.fromisoformat(start) + timedelta(days=60)).isoformat()
        items = quizzes_dynamo.list_range(start, end)

    complete = sum(
        1 for i in items
        if len(i.get('questionIds') or []) == constants.QUIZ_LENGTH)

    return success_response({
        'quizzes': items,
        'count': len(items),
        'completeCount': complete,
        'incompleteCount': len(items) - complete,
    })
