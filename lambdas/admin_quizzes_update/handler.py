"""
PATCH /admin/quizzes/{date} - swap a question or change status.

Body: {"action": "swap", "index": 2, "questionId": "..."}
      {"action": "status", "status": "scheduled" | "published" | "draft"}

Publishing marks every question in the quiz as used, so it can never resurface
on this calendar date in a later year.
"""

from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import (
    get_path_params,
    parse_body,
    require_fields,
    success_response,
)
from lambdas.common import questions_dynamo, quizzes_dynamo

log = get_logger(__file__)

HANDLER = 'admin_quizzes_update'


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    body = parse_body(event)
    require_fields(body, 'action')

    quiz_date = get_path_params(event).get('date') or body.get('quizDate')
    if not quiz_date:
        raise ValidationError(
            message='quizDate is required',
            handler=HANDLER,
            function='handler',
        )

    action = body['action']

    if action == 'swap':
        require_fields(body, 'index', 'questionId')
        item = quizzes_dynamo.swap_question(
            quiz_date, int(body['index']), body['questionId'])

    elif action == 'status':
        require_fields(body, 'status')
        item = quizzes_dynamo.set_status(quiz_date, body['status'])
        if body['status'] == 'published':
            questions_dynamo.mark_used(item.get('questionIds') or [], quiz_date)
            log.info(f"published {quiz_date}, marked questions used")

    else:
        raise ValidationError(
            message=f"unknown action '{action}'",
            handler=HANDLER,
            function='handler',
        )

    return success_response({'quiz': item})
