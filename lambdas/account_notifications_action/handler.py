"""
POST /account/notifications-action - mark notifications read.

Explicit rather than a side effect of fetching the list: opening a page to see
what is there should not mark everything seen whether or not you looked at it.
"""

from lambdas.common import group_access, notifications_dynamo
from lambdas.common.errors import ValidationError, handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, success_response

log = get_logger(__file__)

HANDLER = 'account_notifications_action'


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    body = parse_body(event)
    action = (body.get('action') or 'read').strip()

    if action != 'read':
        raise ValidationError(message=f"unknown action '{action}'",
                              handler=HANDLER, function='handler')

    ids = body.get('notificationIds')
    marked = notifications_dynamo.mark_read(user_id, ids)
    return success_response({'marked': marked})
