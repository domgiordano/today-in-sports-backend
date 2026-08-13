"""
POST /play/answer - submit one answer, get it graded, get the next question.

The client posts a choice and nothing else. It does not post a score, it does
not post how long it took, and it never held the correct answer to begin with.
Everything that decides points is computed here from the stored session.

Replaying an index is rejected rather than re-graded, so a player cannot retry a
question they got wrong by resending it.
"""

from lambdas.common import plays_dynamo, questions_dynamo, scoring
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, require_fields, success_response
from lambdas.common.play_view import public_question, today_utc

log = get_logger(__file__)

HANDLER = 'play_answer'


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

    # A question already answered cannot be answered again — otherwise a wrong
    # answer is simply resubmitted until it is right.
    if plays_dynamo.already_answered(session, index):
        raise ValidationError(
            message=f'question {index} has already been answered',
            handler=HANDLER, function='handler')

    if index != int(session.get('currentIndex', 0)):
        raise ValidationError(
            message='out-of-order answer',
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

    # Server-stamped elapsed time, and a hint flag read from the session rather
    # than the request. The client is not consulted about anything that changes
    # the score.
    seconds = plays_dynamo.elapsed_since_served(session)
    used_hint = plays_dynamo.hint_used(session, index)
    taken_clues = plays_dynamo.clues_taken(session, index)
    result = scoring.grade(question, body.get('answer'), seconds, used_hint,
                           taken_clues)

    session = plays_dynamo.record_answer(identity, quiz_date, index, body.get('answer'), result)

    payload = {
        'quizDate': quiz_date,
        'index': index,
        'correct': result['correct'],
        'credit': result['credit'],
        'points': result['points'],
        'accuracyPoints': result['accuracyPoints'],
        'timeBonus': result['timeBonus'],
        'hintUsed': result['hintUsed'],
        'cluesTaken': result['cluesTaken'],
        'seconds': result['seconds'],
        'totalPoints': int(session.get('totalPoints', 0)),
        # Revealed only now that the answer is locked in.
        'correctAnswer': question.get('answer'),
        'sourceUrl': question.get('sourceDatasetRef'),
        # Where a map question's pin actually was, and what it is called. Held
        # back until this point for the same reason the coordinate is.
        'venueName': question.get('venueName'),
        'venuePlace': question.get('venuePlace'),
    }

    next_index = index + 1
    if next_index >= len(question_ids):
        session = plays_dynamo.complete_session(identity, quiz_date)
        payload['state'] = 'complete'
        payload['correctCount'] = int(session.get('correctCount', 0))
        payload['total'] = len(question_ids)
        log.info(f'{quiz_date} complete: {payload["totalPoints"]} points')
        return success_response(payload)

    next_question = questions_dynamo.get_question(question_ids[next_index])
    plays_dynamo.mark_served(identity, quiz_date, next_index)
    payload['state'] = 'playing'
    payload['question'] = public_question(next_question, next_index, len(question_ids))
    return success_response(payload)
