"""
GET /admin/errors - what is currently broken.

The error-handling decorator has been calling into the request log on every
request since it was ported, inside a try/except that swallows anything it
raises. The module it called did not exist and the table was never provisioned,
so every one of those writes has been a no-op and the product has had no
operational view of its own API.

This is the read side. Failures first, because that is what the screen is for,
with successes available for context when a spike needs explaining.
"""

from lambdas.common import request_log_dynamo
from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'admin_errors'

VALID_BUCKETS = ('error', 'rejected', 'ok')
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    bucket = params.get('bucket', 'error')
    if bucket not in VALID_BUCKETS:
        bucket = 'error'

    limit = min(int(params.get('limit', DEFAULT_LIMIT)), MAX_LIMIT)

    rows = request_log_dynamo.recent(bucket, limit)

    # A per-path tally, because "which endpoint is failing" is the first
    # question anyone asks and counting it by eye down a list of 100 rows is
    # exactly the work a screen should be doing.
    by_path = {}
    for row in rows:
        key = f"{row.get('method', '?')} {row.get('path', '?')}"
        by_path[key] = by_path.get(key, 0) + 1

    return success_response({
        'bucket': bucket,
        'count': len(rows),
        'rows': rows,
        'byPath': sorted(
            ({'path': p, 'count': c} for p, c in by_path.items()),
            key=lambda x: -x['count']),
    })
