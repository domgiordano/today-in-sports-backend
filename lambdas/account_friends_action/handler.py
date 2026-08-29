"""
POST /account/friends-action - add, accept, decline, withdraw, unfriend.

You add somebody by their @handle. There is no directory and no search, for the
same reason groups have no browse: the only way to reach a player is to already
know who they are.
"""

from lambdas.common import friends_dynamo, group_access, notifications_dynamo
from lambdas.common import usernames_dynamo, users_dynamo
from lambdas.common.errors import NotFoundError, ValidationError, handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'account_friends_action'

ACTIONS = ('request', 'accept', 'remove')


def _target(body, user_id):
    """
    Resolve whoever the caller named, by handle or by id.

    An unknown handle and a handle belonging to nobody are the same answer on
    purpose: "no player with that handle" tells you nothing you could not have
    guessed, whereas distinguishing them turns this into a way to test which
    handles exist.
    """
    handle = (body.get('username') or '').strip().lstrip('@')
    target_id = (body.get('userId') or '').strip()

    if handle:
        target_id = usernames_dynamo.owner_of(handle)
    if not target_id:
        raise NotFoundError(message='no player with that handle',
                            handler=HANDLER, function='handler')
    if target_id == user_id:
        raise ValidationError(message='you cannot add yourself',
                              handler=HANDLER, function='handler')
    return target_id


def _name(user_id):
    return (users_dynamo.get_user(user_id) or {}).get('displayName') or 'Somebody'


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    body = parse_body(event)
    action = (body.get('action') or '').strip()

    if action not in ACTIONS:
        raise ValidationError(message=f"unknown action '{action}'",
                              handler=HANDLER, function='handler')

    target_id = _target(body, user_id)

    try:
        if action == 'request':
            status = friends_dynamo.request(user_id, target_id)
            # Told either way: a request needs an answer, and an acceptance is
            # the answer to one they already sent.
            kind = (notifications_dynamo.FRIEND_ACCEPTED
                    if status == friends_dynamo.ACCEPTED
                    else notifications_dynamo.FRIEND_REQUEST)
            _notify(target_id, kind, user_id)
            return success_response({'status': status})

        if action == 'accept':
            friends_dynamo.accept(user_id, target_id)
            _notify(target_id, notifications_dynamo.FRIEND_ACCEPTED, user_id)
            return success_response({'status': friends_dynamo.ACCEPTED})

        # Declining, withdrawing and unfriending are one write and one silence.
        # There is no version of "they said no" worth putting in somebody's
        # notifications.
        friends_dynamo.remove(user_id, target_id)
        return success_response({'status': 'removed'})

    except ValueError as exc:
        raise ValidationError(message=str(exc), handler=HANDLER,
                              function='handler') from exc


def _notify(target_id, kind, actor_id):
    """A friendship that stands is worth more than a notification that sends."""
    try:
        notifications_dynamo.notify([target_id], kind, actor_id)
    except Exception as exc:  # noqa: BLE001
        log.warning(f'friend action saved but notifying failed: {exc}')
