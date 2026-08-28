"""
POST /account/profile - update your own display name or region.

Region is self-declared and coarse: a country, and optionally a state. It exists
so a leaderboard can be filtered, and nothing finer is collected - no city, no
county, no coordinates - because that would be more location data than a trivia
game can justify holding against a named account.
"""

from lambdas.common import users_dynamo
from lambdas.common import usernames_dynamo
from lambdas.common.errors import (
    UnauthorizedError,
    ValidationError,
    handle_errors,
)
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'account_profile'


@handle_errors(HANDLER)
def handler(event, context):
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    claims = claims.get('claims') or claims
    user_id = claims.get('sub')
    if not user_id:
        raise UnauthorizedError(
            message='sign in to change your profile',
            handler=HANDLER, function='handler')

    body = parse_body(event)
    users_dynamo.ensure_user(user_id, claims.get('email'))

    updated = None

    if body.get('displayName'):
        updated = users_dynamo.set_display_name(
            user_id, str(body['displayName']).strip()[:40])

    if 'username' in body:
        # Claimed before anything else is written. A profile that took the
        # display name and then failed on the handle would leave the player
        # half-onboarded with no signal about which half.
        try:
            usernames_dynamo.claim(body['username'], user_id)
        except ValueError as exc:
            raise ValidationError(message=str(exc), handler=HANDLER,
                                  function='handler') from exc

    if 'country' in body:
        country = body.get('country')
        if not country:
            # Somebody who told us where they are should be able to stop
            # telling us without deleting their account.
            updated = users_dynamo.clear_region(user_id)
        else:
            try:
                updated = users_dynamo.set_region(
                    user_id, country, body.get('subdivision'))
            except ValueError as exc:
                raise ValidationError(message=str(exc), handler=HANDLER,
                                      function='handler') from exc

    user = updated or users_dynamo.get_user(user_id) or {}
    return success_response({
        'displayName': user.get('displayName'),
        'username': usernames_dynamo.current_for(user_id),
        'country': user.get('country'),
        'subdivision': user.get('subdivision'),
    })
