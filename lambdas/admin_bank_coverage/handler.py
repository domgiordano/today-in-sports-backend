"""
GET /admin/bank/coverage - approved questions per calendar date.

Drives the review portal's heatmap. The number that matters is not the total but
how evenly it lands: a date-anchored quiz needs roughly 15 per calendar date.
"""

from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response
from lambdas.common import constants, questions_dynamo

log = get_logger(__file__)

HANDLER = 'admin_bank_coverage'

TARGET_PER_DATE = 15


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    status = get_query_params(event).get('status', 'approved')

    counts = questions_dynamo.coverage_counts(status)
    covered = len(counts)
    thin = sum(1 for n in counts.values() if n < TARGET_PER_DATE)

    return success_response({
        'coverage': counts,
        'datesCovered': covered,
        'datesEmpty': 366 - covered,
        'datesThin': thin,
        'datesUnderQuizLength': sum(
            1 for n in counts.values() if n < constants.QUIZ_LENGTH),
        'target': TARGET_PER_DATE,
        'total': sum(counts.values()),
    })
