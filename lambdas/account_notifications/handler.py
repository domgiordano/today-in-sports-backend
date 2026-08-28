"""
GET /account/notifications - what this player has missed.
"""

from lambdas.common import group_access, notifications_dynamo, users_dynamo
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'account_notifications'


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    params = get_query_params(event)
    limit = min(int(params.get('limit') or notifications_dynamo.DEFAULT_LIMIT), 100)

    rows = notifications_dynamo.recent(user_id, limit)

    # One batch for every actor named in the list, rather than a read each.
    actors = users_dynamo.display_names([r.get('actorId') for r in rows])

    return success_response({
        'unread': notifications_dynamo.unread_count(rows),
        'notifications': [{
            'notificationId': r.get('notificationId'),
            'kind': r.get('kind'),
            'actor': actors.get(r.get('actorId')) or 'Somebody',
            'groupId': r.get('groupId'),
            'groupName': r.get('groupName'),
            'quizDate': r.get('quizDate'),
            'commentId': r.get('commentId'),
            'preview': r.get('preview'),
            'read': bool(r.get('read')),
            'createdAt': r.get('createdAt'),
        } for r in rows],
    })
