"""
POST /account/groups-action - create, join, leave or re-code a group.

One handler for four verbs because they share every guard: who is calling, do
they belong, and does the group exist. Splitting them would have duplicated
that three more times.
"""

from lambdas.common import groups_dynamo, users_dynamo
from lambdas.common.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
    handle_errors,
)
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import (
    parse_body,
    require_fields,
    success_response,
)

log = get_logger(__file__)

HANDLER = 'account_groups_action'

ACTIONS = ('create', 'join', 'leave', 'regenerate-code')


@handle_errors(HANDLER)
def handler(event, context):
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    claims = claims.get('claims') or claims
    user_id = claims.get('sub')
    if not user_id:
        raise UnauthorizedError(
            message='sign in to use groups',
            handler=HANDLER, function='handler')

    body = parse_body(event)
    require_fields(body, 'action')
    action = body['action']

    if action not in ACTIONS:
        raise ValidationError(
            message=f'unknown action: {action}',
            handler=HANDLER, function='handler')

    users_dynamo.ensure_user(user_id, claims.get('email'))

    if action == 'create':
        require_fields(body, 'name')
        try:
            group = groups_dynamo.create_group(body['name'], user_id)
        except ValueError as exc:
            raise ValidationError(message=str(exc), handler=HANDLER,
                                  function='handler') from exc
        users_dynamo.add_group(user_id, group['groupId'])
        return success_response(
            {'group': groups_dynamo.public_view(group, include_code=True)})

    if action == 'join':
        require_fields(body, 'inviteCode')
        try:
            group = groups_dynamo.join_group(body['inviteCode'], user_id)
        except ValueError as exc:
            # A bad code and a full group are both the caller's problem to fix,
            # and neither should read as a server fault.
            raise ValidationError(message=str(exc), handler=HANDLER,
                                  function='handler') from exc
        users_dynamo.add_group(user_id, group['groupId'])
        return success_response(
            {'group': groups_dynamo.public_view(group, include_code=True)})

    require_fields(body, 'groupId')
    group_id = body['groupId']
    group = groups_dynamo.get_group(group_id)
    if not group:
        raise NotFoundError(message='no such group', handler=HANDLER,
                            function='handler')

    if user_id not in set(group.get('memberIds') or set()):
        raise ForbiddenError(message='you are not in that group',
                             handler=HANDLER, function='handler')

    if action == 'leave':
        groups_dynamo.leave_group(group_id, user_id)
        users_dynamo.remove_group(user_id, group_id)
        return success_response({'left': group_id})

    try:
        group = groups_dynamo.regenerate_code(group_id, user_id)
    except PermissionError as exc:
        raise ForbiddenError(message=str(exc), handler=HANDLER,
                             function='handler') from exc
    return success_response(
        {'group': groups_dynamo.public_view(group, include_code=True)})
