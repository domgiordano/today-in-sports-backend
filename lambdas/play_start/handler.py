"""
POST /play/start - begin (or resume) today's quiz.

Public: no admin gate. The caller identifies as either a signed-in Cognito
subject or an anonymous device id, and the difference decides leaderboard
eligibility later, not whether they may play.

Returns the first unanswered question **without its answer**, and stamps the
serve time server-side so scoring has an honest clock.
"""

from datetime import datetime, timezone

from lambdas.common import constants, plays_dynamo, questions_dynamo, quizzes_dynamo
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.play_view import public_question, today_utc
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'play_start'


@handle_errors(HANDLER)
def handler(event, context):
    body = parse_body(event)

    identity = (body.get('deviceId') or '').strip()
    anonymous = True

    # A verified Cognito subject always wins over a client-supplied device id.
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    if claims.get('sub'):
        identity = claims['sub']
        anonymous = False

    if not identity:
        raise ValidationError(
            message='deviceId is required when not signed in',
            handler=HANDLER, function='handler')

    quiz_date = body.get('quizDate') or today_utc()

    quiz = quizzes_dynamo.get_quiz(quiz_date)
    if not quiz or quiz.get('status') != 'published':
        raise NotFoundError(
            message=f'no published quiz for {quiz_date}',
            handler=HANDLER, function='handler')

    question_ids = list(quiz.get('questionIds') or [])
    session, created = plays_dynamo.start_session(
        identity, quiz_date, question_ids, anonymous)

    if plays_dynamo.is_complete(session):
        return success_response({
            'quizDate': quiz_date,
            'state': 'complete',
            'totalPoints': int(session.get('totalPoints', 0)),
            'correctCount': int(session.get('correctCount', 0)),
            'total': len(question_ids),
            'anonymous': session.get('anonymous', True),
        })

    index = int(session.get('currentIndex', 0))
    question = questions_dynamo.get_question(question_ids[index])
    if not question:
        raise NotFoundError(
            message='a question in this quiz is missing',
            handler=HANDLER, function='handler')

    plays_dynamo.mark_served(identity, quiz_date, index)
    log.info(f'{"started" if created else "resumed"} {quiz_date} at index {index}')

    return success_response({
        'quizDate': quiz_date,
        'state': 'playing',
        'resumed': not created,
        'anonymous': anonymous,
        'totalPoints': int(session.get('totalPoints', 0)),
        'question': public_question(question, index, len(question_ids)),
    })
