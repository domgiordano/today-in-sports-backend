"""
GET /play/leaderboard - the day's scores.

Public. Anonymous players appear alongside signed-in ones, identified by the
name they chose after finishing.

Worth being clear-eyed about: an anonymous score is only as trustworthy as a
device id, which anyone can reset. That is accepted rather than fought — a daily
board is low stakes, and the alternative is refusing to show a visitor their own
result. Persistent profiles and streaks are what require an account.
"""

from lambdas.common import (groups_dynamo, plays_dynamo, reactions_dynamo,
                            users_dynamo)
from lambdas.common.errors import NotFoundError, handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response
from lambdas.common.play_view import today_utc

log = get_logger(__file__)

HANDLER = 'play_leaderboard'

DEFAULT_LIMIT = 50


def board_name(row, profile_names):
    """
    What to call whoever played this round.

    A signed-in player is named by their profile, looked up here rather than
    copied onto the round when it was played. That is what makes a rename
    retroactive: the name is theirs, not the round's, so changing it in
    settings changes every board they appear on rather than only the ones they
    play next.

    An anonymous player has no profile to read, so the name they typed after
    finishing is stored on the round and used as-is.

    Nothing here derives a name from an email. The client used to fall back to
    the local part when a signed-in player had set no name, which put an
    address fragment on a public board.
    """
    if not row.get('anonymous', True):
        # Stripped here as well as at the lookup. A blank name is not a name
        # wherever it came from, and this function is the last thing between a
        # stored value and a public page.
        named = (profile_names.get(row.get('identity')) or '').strip()
        if named:
            return named
        # Signed in but never chose a name. "Anonymous" is wrong — they are not
        # anonymous, they just have not said what to call them — and their
        # email is not ours to publish.
        return 'Unnamed player'
    return (row.get('displayName') or '').strip() or 'Anonymous'


def public_row(row, position, profile_names, counts, mine):
    play_id = row.get('playId')
    return {
        'position': position,
        # The identity is what a reaction is addressed to. It is already
        # public in the sense that it is the key of a row on a public board,
        # and without it the client cannot say which score it is reacting to.
        'target': row.get('identity'),
        'name': board_name(row, profile_names),
        'points': int(row.get('totalPoints', 0)),
        'correct': int(row.get('correctCount', 0)),
        'anonymous': bool(row.get('anonymous', True)),
        'reactions': counts.get(play_id, {}),
        'yourReaction': mine.get(play_id),
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

    # One batch read for the whole board rather than a lookup per row.
    profile_names = users_dynamo.display_names(
        [r.get('identity') for r in rows if not r.get('anonymous', True)])

    # And one query for the day's reactions rather than one per row.
    counts, by_reactor = reactions_dynamo.for_day(quiz_date)

    viewer = ((event.get('requestContext') or {}).get('authorizer') or {}).get('sub')
    mine = by_reactor.get(viewer, {}) if viewer else {}

    board = [public_row(r, i + 1, profile_names, counts, mine)
             for i, r in enumerate(rows)]

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
                'name': board_name(session, users_dynamo.display_names(
                    [session.get('identity')]
                    if not session.get('anonymous', True) else [])),
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
