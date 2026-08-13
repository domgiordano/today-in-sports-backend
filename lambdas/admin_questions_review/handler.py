"""
POST /admin/questions/{id}/review - approve, reject or edit a question.

Body: {"action": "approve" | "reject" | "edit", "reason": ..., "fields": {...}}
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
from lambdas.common import questions_dynamo

log = get_logger(__file__)

HANDLER = 'admin_questions_review'


@handle_errors(HANDLER)
def handler(event, context):
    reviewer = require_admin(event)
    body = parse_body(event)
    require_fields(body, 'action')

    question_id = get_path_params(event).get('id') or body.get('questionId')
    if not question_id:
        raise ValidationError(
            message='questionId is required',
            handler=HANDLER,
            function='handler',
        )

    action = body['action']

    if action == 'approve':
        item = questions_dynamo.set_status(question_id, 'approved', reviewer)
    elif action == 'reject':
        # A rejection without a reason teaches nothing about why the template
        # or detector produced a bad question.
        reason = body.get('reason')
        if not reason:
            raise ValidationError(
                message='a rejection requires a reason',
                handler=HANDLER,
                function='handler',
            )
        item = questions_dynamo.set_status(question_id, 'rejected', reviewer, reason)
    elif action == 'edit':
        item = questions_dynamo.apply_edit(question_id, body.get('fields'), reviewer)
    else:
        raise ValidationError(
            message=f"unknown action '{action}'",
            handler=HANDLER,
            function='handler',
        )

    return success_response({'question': item})
