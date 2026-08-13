"""
POST /play/name - put a name to a finished round.

Only accepted once the quiz is complete. Naming yourself mid-quiz would let a
player see how they were doing before deciding whether to be identified, which
is a small thing that quietly distorts a leaderboard.
"""

from lambdas.common import plays_dynamo
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, require_fields, success_response
from lambdas.play_start.handler import today_utc

log = get_logger(__file__)

HANDLER = 'play_name'


@handle_errors(HANDLER)
def handler(event, context):
    body = parse_body(event)
    require_fields(body, 'name')

    identity = (body.get('deviceId') or '').strip()
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    if claims.get('sub'):
        identity = claims['sub']
    if not identity:
        raise ValidationError(
            message='deviceId is required when not signed in',
            handler=HANDLER, function='handler')

    quiz_date = body.get('quizDate') or today_utc()

    try:
        session = plays_dynamo.set_display_name(identity, quiz_date, body['name'])
    except ValueError as exc:
        raise ValidationError(message=str(exc), handler=HANDLER, function='handler')
    except Exception as exc:
        # The conditional write fails when the session is missing or unfinished.
        if 'ConditionalCheckFailed' in type(exc).__name__ or 'ConditionalCheckFailed' in str(exc):
            raise NotFoundError(
                message='no finished round for today to name',
                handler=HANDLER, function='handler')
        raise

    return success_response({
        'quizDate': quiz_date,
        'name': session.get('displayName'),
        'points': int(session.get('totalPoints', 0)),
    })
