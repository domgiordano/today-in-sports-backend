"""
GET /me - the signed-in player's own record.

Everything a profile page shows: streak, badges, lifetime totals. Creates the
record on first call, which is the lazy upsert the whole design rests on - a
Cognito trigger would be a second deploy target with a failure mode where
sign-up succeeds and the profile silently does not.
"""

from lambdas.common import badges, users_dynamo
from lambdas.common.errors import handle_errors, UnauthorizedError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'me'


@handle_errors(HANDLER)
def handler(event, context):
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    claims = claims.get('claims') or claims

    user_id = claims.get('sub')
    if not user_id:
        raise UnauthorizedError(
            message='sign in to see your profile',
            handler=HANDLER, function='handler')

    user = users_dynamo.ensure_user(
        user_id, claims.get('email'), claims.get('nickname'))

    held = list(user.get('badges') or [])

    return success_response({
        'userId': user_id,
        'email': user.get('email'),
        'displayName': user.get('displayName'),
        'createdAt': user.get('createdAt'),
        'playCount': int(user.get('playCount') or 0),
        'currentStreak': int(user.get('currentStreak') or 0),
        'longestStreak': int(user.get('longestStreak') or 0),
        'totalPoints': int(user.get('totalPoints') or 0),
        'totalCorrect': int(user.get('totalCorrect') or 0),
        'lastPlayedDate': user.get('lastPlayedDate'),
        'country': user.get('country'),
        'subdivision': user.get('subdivision'),
        'badges': badges.describe(held),
        # The full catalogue too, so a profile can show what is still to earn
        # rather than only what has been.
        'allBadges': badges.CATALOGUE,
    })
