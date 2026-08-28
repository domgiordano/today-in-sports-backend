"""
GET /account/comments - what a group said about a day.

Members only, and "not a member" answers the same 404 as "no such group" so the
endpoint cannot be used to find out which groups exist.
"""

from lambdas.common import comments_dynamo, group_access, usernames_dynamo, users_dynamo
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.play_view import today_utc
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'account_comments'


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    params = get_query_params(event)

    group = group_access.group_for_member(
        params.get('groupId'), user_id, HANDLER)
    quiz_date = params.get('quizDate') or today_utc()

    rows = comments_dynamo.for_thread(group['groupId'], quiz_date)

    # Names resolved at read time, in one batch, for the same reason the
    # leaderboard does it: a name belongs to the person, so somebody who
    # renames themselves is renamed on everything they have ever written
    # rather than only on what they write next.
    authors = users_dynamo.display_names([r.get('authorId') for r in rows])

    return success_response({
        'groupId': group['groupId'],
        'quizDate': quiz_date,
        'comments': [{
            'commentId': r.get('commentId'),
            'authorId': r.get('authorId'),
            'author': authors.get(r.get('authorId')) or 'Unnamed player',
            'authorUsername': usernames_dynamo.current_for(r.get('authorId')),
            'body': r.get('body'),
            'postedAt': r.get('postedAt'),
            'yours': r.get('authorId') == user_id,
            # So a comment addressed to the reader can be marked as such
            # without the client re-parsing the body and reaching a different
            # answer to the one the server stored.
            'mentionsYou': user_id in (r.get('mentions') or []),
            'canDelete': comments_dynamo.may_delete(r, user_id, group),
        } for r in rows],
        'maxLength': comments_dynamo.MAX_LENGTH,
    })
