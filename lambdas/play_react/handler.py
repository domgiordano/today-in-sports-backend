"""
POST /play/react - leave, change or clear an emoji on somebody's round.

Signed in only. An anonymous identity is a device id that anyone can reset, so
anonymous reactions would count browser sessions rather than people.

Reacting to your own round is allowed. It reads as odd to forbid it, and a
group of four where one person cannot join in is worse than a group of four
where somebody applauds themselves.
"""

from lambdas.common import identity as identity_mod
from lambdas.common import notifications_dynamo, plays_dynamo, reactions_dynamo
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.play_view import today_utc
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'play_react'


@handle_errors(HANDLER)
def handler(event, context):
    body = parse_body(event)

    # Verified from the bearer token: this route is public, so API Gateway
    # does not populate claims on it.
    claims = {'sub': identity_mod.subject(event)}
    reactor = claims.get('sub')
    if not reactor:
        raise ValidationError(
            message='sign in to react to a score',
            handler=HANDLER, function='handler')

    target = (body.get('target') or '').strip()
    if not target:
        raise ValidationError(
            message='target is required', handler=HANDLER, function='handler')

    quiz_date = body.get('quizDate') or today_utc()
    play_id = plays_dynamo.session_key(target, quiz_date)

    # A reaction to a round that does not exist, or that nobody finished, is a
    # row pointing at nothing — and it would let a caller probe which
    # identities have played.
    session = plays_dynamo.get_session(target, quiz_date)
    if not session or not session.get('completedAt'):
        raise NotFoundError(
            message='no finished round to react to',
            handler=HANDLER, function='handler')

    try:
        now = reactions_dynamo.set_reaction(
            play_id, quiz_date, reactor, body.get('emoji'))
    except ValueError as exc:
        raise ValidationError(
            message=str(exc), handler=HANDLER, function='handler')

    # Only on adding one. Clearing a reaction is not news, and telling somebody
    # that their applause was withdrawn is worse than saying nothing.
    if now and target != reactor:
        try:
            notifications_dynamo.notify(
                [target], notifications_dynamo.REACTION, reactor,
                quiz_date=quiz_date, body=now)
        except Exception as exc:  # noqa: BLE001 - the reaction still stands
            log.warning(f'reaction saved but notifying failed: {exc}')

    return success_response({
        'target': target,
        'quizDate': quiz_date,
        'emoji': now,
        'available': list(reactions_dynamo.ALLOWED),
    })
