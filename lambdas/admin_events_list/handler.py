"""
GET /admin/events - browse detected events.

Params: mmdd, sport, minScore, limit.

Events are the layer between raw games and questions: a detector decided this
moment mattered. Browsing them is how you tell "the generator is weak" from
"the detectors found nothing here".
"""

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response
from lambdas.common import constants

log = get_logger(__file__)

HANDLER = 'admin_events_list'

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource('dynamodb')
    return _dynamo.Table(constants.EVENTS_TABLE_NAME)


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    mmdd = params.get('mmdd')
    if not mmdd:
        raise ValidationError(
            message='mmdd is required (events are partitioned by calendar date)',
            handler=HANDLER,
            function='handler',
        )

    limit = min(int(params.get('limit', 100)), 500)
    resp = _table().query(
        KeyConditionExpression=Key('mmdd').eq(mmdd),
        Limit=limit,
    )
    items = resp.get('Items', [])

    sport = params.get('sport')
    if sport:
        items = [i for i in items if i.get('sport') == sport]

    min_score = params.get('minScore')
    if min_score:
        items = [i for i in items
                 if (i.get('notabilityScore') or 0) >= int(min_score)]

    items.sort(key=lambda i: -(i.get('notabilityScore') or 0))

    return success_response({
        'events': items,
        'count': len(items),
        'mmdd': mmdd,
    })
