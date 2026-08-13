"""
GET /play/leaderboard - the day's scores.

Public. Anonymous players appear alongside signed-in ones, identified by the
name they chose after finishing.

Worth being clear-eyed about: an anonymous score is only as trustworthy as a
device id, which anyone can reset. That is accepted rather than fought — a daily
board is low stakes, and the alternative is refusing to show a visitor their own
result. Persistent profiles and streaks are what require an account.
"""

from lambdas.common import groups_dynamo, plays_dynamo
from lambdas.common.errors import NotFoundError, handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response
from lambdas.common.play_view import today_utc

log = get_logger(__file__)

HANDLER = 'play_leaderboard'

DEFAULT_LIMIT = 50


def public_row(row, position):
    return {
        'position': position,
        'name': row.get('displayName') or 'Anonymous',
        'points': int(row.get('totalPoints', 0)),
        'correct': int(row.get('correctCount', 0)),
        'anonymous': bool(row.get('anonymous', True)),
    }


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    quiz_date = params.get('quizDate') or today_utc()
    limit = int(params.get('limit', DEFAULT_LIMIT))

    # A group board is the same day's scores with a membership filter, not a
    # separate scoring system. It is fetched deeper than it is shown, because
    # a group of ten can easily sit outside the global top fifty and would
    # otherwise come back empty.
    group_id = params.get('groupId')
    group = None
    if group_id:
        group = groups_dynamo.get_group(group_id)
        if not group:
            raise NotFoundError(
                message='no such group', handler=HANDLER, function='handler')

    if group:
        rows = plays_dynamo.sessions_for(
            group.get('memberIds') or set(), quiz_date)[:limit]
    else:
        rows = plays_dynamo.leaderboard(quiz_date, limit)

    board = [public_row(r, i + 1) for i, r in enumerate(rows)]

    # A caller can ask where a specific score would land without being on the
    # visible board — which is how an anonymous player sees their standing.
    you = None
    identity = params.get('deviceId')
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    if claims.get('sub'):
        identity = claims['sub']

    if identity:
        session = plays_dynamo.get_session(identity, quiz_date)
        if session and session.get('completedAt'):
            points = int(session.get('totalPoints', 0))
            you = {
                'points': points,
                'correct': int(session.get('correctCount', 0)),
                'rank': plays_dynamo.rank_for(quiz_date, points),
                'name': session.get('displayName'),
                'anonymous': bool(session.get('anonymous', True)),
            }

    return success_response({
        'quizDate': quiz_date,
        'leaderboard': board,
        'players': len(board),
        'you': you,
        'scope': 'group' if group else 'global',
        'groupName': group.get('name') if group else None,
    })
