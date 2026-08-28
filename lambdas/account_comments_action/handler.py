"""
POST /account/comments-action - post or delete a comment.

One route for both because they share everything that matters: the caller, the
membership check, and the thread. Splitting them would duplicate the check,
and a duplicated permission check is one that eventually disagrees with itself.
"""

from lambdas.common import comments_dynamo, group_access
from lambdas.common.errors import (ForbiddenError, NotFoundError,
                                   ValidationError, handle_errors)
from lambdas.common.logger import get_logger
from lambdas.common.play_view import today_utc
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'account_comments_action'


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    body = parse_body(event)

    group = group_access.group_for_member(body.get('groupId'), user_id, HANDLER)
    quiz_date = body.get('quizDate') or today_utc()
    action = (body.get('action') or 'post').strip()

    if action == 'post':
        try:
            row = comments_dynamo.post(
                group['groupId'], quiz_date, user_id, body.get('body'))
        except ValueError as exc:
            raise ValidationError(message=str(exc), handler=HANDLER,
                                  function='handler') from exc
        return success_response({'commentId': row['commentId'],
                                 'postedAt': row['postedAt']})

    if action == 'delete':
        comment_id = (body.get('commentId') or '').strip()
        existing = comments_dynamo.find(
            group['groupId'], quiz_date, comment_id)
        if not existing:
            raise NotFoundError(message='no such comment', handler=HANDLER,
                                function='handler')
        if not comments_dynamo.may_delete(existing, user_id, group):
            raise ForbiddenError(
                message='you can only delete your own comments',
                handler=HANDLER, function='handler')
        comments_dynamo.delete(group['groupId'], quiz_date, comment_id)
        return success_response({'deleted': comment_id})

    raise ValidationError(message=f"unknown action '{action}'",
                          handler=HANDLER, function='handler')
