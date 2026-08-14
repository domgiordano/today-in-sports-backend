"""
GET /account/groups - the groups this player belongs to, and how each is doing.

Only the caller's own groups. There is deliberately no endpoint answering
"what groups exist": a searchable directory of small private groups is a
harassment surface with no upside for a trivia game.

Each group carries its rolled-up numbers, which is why group analytics live
here rather than on the public stats route. Membership is the permission, and
this is the only endpoint that already knows it.
"""

from lambdas.common import groups_dynamo, stats_dynamo, users_dynamo
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
        'groups': [_with_stats(g) for g in groups],
        'maxMembers': groups_dynamo.MAX_MEMBERS,
    })


# What a group screen may show. Deliberately the same fields the public page
# shows for everyone, so a group is a comparison and not a different report.
STAT_FIELDS = (
    'rounds', 'players', 'avgPoints', 'avgCorrect',
    'perfectRounds', 'avgSeconds', 'bestPoints', 'bySport',
)


def _with_stats(group):
    view = groups_dynamo.public_view(group, include_code=True)
    rollup = stats_dynamo.get_rollup(f"group#{group['groupId']}", 'all')
    # None rather than zeroes: a group that has not played yet has no numbers,
    # and zeroes would read as a real and very bad result.
    view['stats'] = (
        {field: rollup.get(field) for field in STAT_FIELDS} if rollup else None)
    return view
