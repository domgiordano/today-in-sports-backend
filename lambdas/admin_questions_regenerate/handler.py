"""
POST /admin/questions/{id}/regenerate - re-queue a question's source event.

Marks the question rejected and flags its event for regeneration. Generation
itself runs offline in scripts/generate_questions.py, never in a Lambda: keeping
it out of the request path is what holds runtime cost at zero and guarantees a
bad batch can never reach a player.
"""

import boto3

from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors, NotFoundError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import (
    get_path_params,
    parse_body,
    success_response,
)
from lambdas.common import constants, questions_dynamo

log = get_logger(__file__)

HANDLER = 'admin_questions_regenerate'

_dynamo = None


def _events_table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource('dynamodb')
    return _dynamo.Table(constants.EVENTS_TABLE_NAME)


@handle_errors(HANDLER)
def handler(event, context):
    reviewer = require_admin(event)
    body = parse_body(event)
    question_id = get_path_params(event).get('id') or body.get('questionId')

    question = questions_dynamo.get_question(question_id)
    if not question:
        raise NotFoundError(
            message=f'no question {question_id}',
            handler=HANDLER,
            function='handler',
        )

    reason = body.get('reason') or 'queued for regeneration'
    questions_dynamo.set_status(question_id, 'rejected', reviewer, reason)

    # Flag the source event so the next offline run knows to try again.
    flagged = False
    if question.get('mmdd') and question.get('year') and question.get('sourceEventId'):
        try:
            _events_table().update_item(
                Key={
                    'mmdd': question['mmdd'],
                    'yearEventId': f"{question['year']}#{question['sourceEventId']}",
                },
                UpdateExpression='SET needsRegeneration = :t',
                ExpressionAttributeValues={':t': True},
            )
            flagged = True
        except Exception as exc:
            log.warning(f'could not flag event for {question_id}: {exc}')

    return success_response({
        'questionId': question_id,
        'status': 'rejected',
        'eventFlagged': flagged,
    })
