"""
GET /admin/users - who is playing.

Who signed in, when they were last seen, how many quizzes they have played,
their streak and which groups they are in. The operational counterpart to the
errors panel: one screen answers "is anything broken", this one answers "is
anyone here".
"""

from lambdas.common import users_dynamo
from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'admin_users'

DEFAULT_LIMIT = 200


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)
    limit = min(int(params.get('limit', DEFAULT_LIMIT)), 500)

    users = users_dynamo.list_users(limit)

    rows = [{
        'userId': u.get('userId'),
        'email': u.get('email'),
        'displayName': u.get('displayName'),
        'createdAt': u.get('createdAt'),
        'lastSeenAt': u.get('lastSeenAt'),
        'lastPlayedDate': u.get('lastPlayedDate'),
        'playCount': int(u.get('playCount') or 0),
        'currentStreak': int(u.get('currentStreak') or 0),
        'longestStreak': int(u.get('longestStreak') or 0),
        'totalPoints': int(u.get('totalPoints') or 0),
        'badgeCount': len(u.get('badges') or []),
        'groupIds': sorted(u.get('groupIds') or []),
    } for u in users]

    rows.sort(key=lambda r: r.get('lastSeenAt') or '', reverse=True)

    played = [r for r in rows if r['playCount']]
    return success_response({
        'count': len(rows),
        'users': rows,
        # The numbers worth seeing without adding up a column by eye.
        'summary': {
            'total': len(rows),
            'everPlayed': len(played),
            'playingToday': len([r for r in rows if r['lastPlayedDate']]),
            'onAStreak': len([r for r in rows if r['currentStreak'] > 1]),
        },
    })
