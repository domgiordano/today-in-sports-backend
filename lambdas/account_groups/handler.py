"""
GET /account/groups - the groups this player belongs to.

Only the caller's own groups. There is deliberately no endpoint answering
"what groups exist": a searchable directory of small private groups is a
harassment surface with no upside for a trivia game.
"""

from lambdas.common import groups_dynamo, users_dynamo
from lambdas.common.errors import handle_errors, UnauthorizedError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'account_groups'


def caller(event):
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    claims = claims.get('claims') or claims
    user_id = claims.get('sub')
    if not user_id:
        raise UnauthorizedError(
            message='sign in to use groups',
            handler=HANDLER, function='caller')
    return user_id, claims


@handle_errors(HANDLER)
def handler(event, context):
    user_id, claims = caller(event)
    user = users_dynamo.ensure_user(user_id, claims.get('email'))

    groups = groups_dynamo.groups_for(user.get('groupIds') or set())

    return success_response({
        # The code travels because the caller is already a member of every
        # group in this list.
        'groups': [groups_dynamo.public_view(g, include_code=True)
                   for g in groups],
        'maxMembers': groups_dynamo.MAX_MEMBERS,
    })
