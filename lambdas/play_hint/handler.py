"""
POST /play/hint - trade points for the multiple-choice options.

Questions that hold a set of choices are served as free response: type the name
and earn full credit, or ask for the four options and earn a fraction of it.
Recall is a harder act than recognition and the scoring says so.

The options are released only here, and asking for them is what records the
hint. That is the whole point of a separate endpoint - if the choices shipped
with the question, whether the player looked at them would be a fact only the
client knew, and the score would rest on the honesty of code we do not control.

Asking twice costs the same as asking once. A refresh is not a second penalty.
"""

from lambdas.common import plays_dynamo, questions_dynamo, scoring
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.play_view import options_for, today_utc
from lambdas.common.utility_helpers import (
    parse_body,
    require_fields,
    success_response,
)

log = get_logger(__file__)

HANDLER = 'play_hint'


@handle_errors(HANDLER)
def handler(event, context):
    body = parse_body(event)
    require_fields(body, 'index')

    identity = (body.get('deviceId') or '').strip()
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    if claims.get('sub'):
        identity = claims['sub']

    if not identity:
        raise ValidationError(
            message='deviceId is required when not signed in',
            handler=HANDLER, function='handler')

    quiz_date = body.get('quizDate') or today_utc()
    index = int(body['index'])

    session = plays_dynamo.get_session(identity, quiz_date)
    if not session:
        raise NotFoundError(
            message='no play session; start the quiz first',
            handler=HANDLER, function='handler')

    if plays_dynamo.is_complete(session):
        raise ValidationError(
            message='this quiz is already complete',
            handler=HANDLER, function='handler')

    # A hint after the fact would be free: the question is already scored.
    if plays_dynamo.already_answered(session, index):
        raise ValidationError(
            message=f'question {index} has already been answered',
            handler=HANDLER, function='handler')

    if index != int(session.get('currentIndex', 0)):
        raise ValidationError(
            message='hint requested for a question that is not in play',
            handler=HANDLER, function='handler')

    question_ids = list(session.get('questionIds') or [])
    if index >= len(question_ids):
        raise ValidationError(
            message='index beyond the end of this quiz',
            handler=HANDLER, function='handler')

    question = questions_dynamo.get_question(question_ids[index])
    if not question:
        raise NotFoundError(
            message='question not found',
            handler=HANDLER, function='handler')

    options = options_for(question)
    if not options:
        raise ValidationError(
            message='this question has no options to offer',
            handler=HANDLER, function='handler')

    plays_dynamo.record_hint(identity, quiz_date, index)
    log.info(f'hint taken on {quiz_date} index {index}')

    return success_response({
        'quizDate': quiz_date,
        'index': index,
        'options': options,
        # Stated plainly so the cost is known before answering, not discovered
        # in the results.
        'creditMultiplier': scoring.HINT_CREDIT,
    })
