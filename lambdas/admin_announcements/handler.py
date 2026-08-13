"""
POST /admin/announcements - write one, or take one down.

Creating enforces an end date, because an announcement without one runs
forever after you forget about it, and a stale banner teaches people to ignore
the channel entirely.
"""

from lambdas.common import announcements_dynamo as announcements
from lambdas.common.admin import require_admin
from lambdas.common.errors import ValidationError, handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import (
    parse_body,
    require_fields,
    success_response,
)

log = get_logger(__file__)

HANDLER = 'admin_announcements'


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    body = parse_body(event)

    action = body.get('action', 'create')

    if action == 'list':
        rows = announcements.list_all()
        return success_response({
            'announcements': rows,
            'active': [r['announcementId'] for r in announcements.active()],
            'placements': list(announcements.VALID_PLACEMENTS),
            'severities': list(announcements.SEVERITIES),
        })

    if action == 'end':
        require_fields(body, 'announcementId')
        row = announcements.end_now(body['announcementId'])
        return success_response({'announcement': row})

    require_fields(body, 'title')
    try:
        row = announcements.create(
            body['title'],
            body.get('body'),
            body.get('severity', 'info'),
            body.get('placements'),
            body.get('runDays'),
            body.get('dismissible', True),
        )
    except ValueError as exc:
        raise ValidationError(message=str(exc), handler=HANDLER,
                              function='handler') from exc

    return success_response({'announcement': row})
