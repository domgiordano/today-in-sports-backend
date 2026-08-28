"""
POST /account/comments-action - post or delete a comment.

One route for both because they share everything that matters: the caller, the
membership check, and the thread. Splitting them would duplicate the check,
and a duplicated permission check is one that eventually disagrees with itself.
"""

from lambdas.common import (comments_dynamo, group_access,
                            notifications_dynamo, usernames_dynamo)
from lambdas.common.errors import (ForbiddenError, NotFoundError,
                                   ValidationError, handle_errors)
from lambdas.common.logger import get_logger
from lambdas.common.play_view import today_utc
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'account_comments_action'


def _notify(group, quiz_date, author_id, row, mentions):
    """
    Who hears about a new comment.

    Mentions always. Beyond that, only people already in this conversation —
    somebody who has posted in the same day's thread. Notifying every member of
    every comment means fifty notifications for one sentence in a group of
    fifty, and the reasonable answer to that is to mute the group.

    Failures here are swallowed. A notification that does not send is a
    disappointment; a comment that fails to post because a notification did is
    a bug, and the comment is the thing the player asked for.
    """
    try:
        if mentions:
            notifications_dynamo.notify(
                mentions, notifications_dynamo.MENTION, author_id,
                group_id=group['groupId'], group_name=group.get('name'),
                quiz_date=quiz_date, body=row.get('body'),
                comment_id=row.get('commentId'))

        already_here = {c.get('authorId')
                        for c in comments_dynamo.for_thread(
                            group['groupId'], quiz_date)}
        replied_to = already_here - set(mentions or []) - {author_id}
        if replied_to:
            notifications_dynamo.notify(
                replied_to, notifications_dynamo.REPLY, author_id,
                group_id=group['groupId'], group_name=group.get('name'),
                quiz_date=quiz_date, body=row.get('body'),
                comment_id=row.get('commentId'))
    except Exception as exc:  # noqa: BLE001 - never fail the comment for this
        log.warning(f'comment posted but notifying failed: {exc}')


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    body = parse_body(event)

    group = group_access.group_for_member(body.get('groupId'), user_id, HANDLER)
    quiz_date = body.get('quizDate') or today_utc()
    action = (body.get('action') or 'post').strip()

    if action == 'post':
        # Resolved against this group's members only. An @handle belonging to
        # somebody outside resolves to nothing rather than to them — otherwise
        # a private group becomes a way of reaching anybody whose handle you
        # can guess.
        handles = usernames_dynamo.handles_for(group.get('memberIds') or set())
        mentions = comments_dynamo.find_mentions(body.get('body'), handles)
        try:
            row = comments_dynamo.post(
                group['groupId'], quiz_date, user_id, body.get('body'),
                mentions=mentions)
        except ValueError as exc:
            raise ValidationError(message=str(exc), handler=HANDLER,
                                  function='handler') from exc
        _notify(group, quiz_date, user_id, row, mentions)

        return success_response({'commentId': row['commentId'],
                                 'postedAt': row['postedAt'],
                                 'mentioned': mentions})

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
