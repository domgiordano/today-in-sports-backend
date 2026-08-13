"""
GET /play/announcements - what to tell the player.

Public, because an announcement about the product is not privileged
information and anonymous players should see it too.

`placement` decides where it may appear, and the only valid answers are the
landing page and the results screen. There is deliberately no placement that
interrupts a quiz in progress.
"""

from lambdas.common import announcements_dynamo as announcements
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'play_announcements'


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    placement = params.get('placement')
    if placement not in announcements.VALID_PLACEMENTS:
        placement = None

    rows = announcements.active(placement)

    return success_response({
        'announcements': [announcements.public_view(r) for r in rows],
        'count': len(rows),
    })
