"""
GET /account/friends - who you play against, and how you are all doing.

A friends board rather than a list. A list of names answers nothing a player
wanted to know; the reason to add somebody is to see whether you beat them
today, so the names arrive already ranked and already carrying the day.
"""

from lambdas.common import (
    friends_dynamo, group_access, plays_dynamo, users_dynamo, usernames_dynamo,
)
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.play_view import today_utc
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'account_friends'


def _person(user_id, profile, handle, session):
    """One row: who they are, their season, and today."""
    played = bool(session and session.get('completedAt'))
    return {
        'userId': user_id,
        'displayName': (profile or {}).get('displayName') or 'Somebody',
        'username': handle,
        'totalPoints': int((profile or {}).get('totalPoints') or 0),
        'playCount': int((profile or {}).get('playCount') or 0),
        'currentStreak': int((profile or {}).get('currentStreak') or 0),
        # Null, not zero — a friend who has not played yet has not scored
        # nothing, and a board that cannot tell them apart will rank them last.
        'todayPoints': int(session.get('totalPoints') or 0) if played else None,
        'todayCorrect': int(session.get('correctCount') or 0) if played else None,
        'playedToday': played,
    }


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    rows = friends_dynamo.for_user(user_id)

    # Everybody named, in one batch each, rather than a read per person.
    ids = [r['friendId'] for r in rows] + [user_id]
    profiles = users_dynamo.profiles(ids)
    # `handles_for` is keyed BY HANDLE — it exists to resolve @mentions, which
    # go the other way. Inverted here rather than called per person: it already
    # costs one query each, so this is the same work with one dict instead of a
    # lookup that silently returns None for every user.
    handles = {uid: handle
               for handle, uid in usernames_dynamo.handles_for(ids).items()}

    quiz_date = today_utc()
    sessions = {i: plays_dynamo.get_session(i, quiz_date) for i in ids}

    def person(uid):
        return _person(uid, profiles.get(uid), handles.get(uid), sessions.get(uid))

    friends = [person(r['friendId']) for r in rows
               if r.get('status') == friends_dynamo.ACCEPTED]

    # You are on your own board. A ranking that leaves out the person reading
    # it makes them do the comparison themselves, which is the one thing the
    # board exists to save them.
    board = sorted(friends + [person(user_id)],
                   key=lambda p: (p['todayPoints'] is None, -(p['todayPoints'] or 0)))
    for position, row in enumerate(board, start=1):
        row['position'] = position
        row['isYou'] = row['userId'] == user_id

    return success_response({
        'quizDate': quiz_date,
        'friends': friends,
        'board': board,
        'incoming': [person(r['friendId']) for r in rows
                     if r.get('status') == friends_dynamo.PENDING_IN],
        'outgoing': [person(r['friendId']) for r in rows
                     if r.get('status') == friends_dynamo.PENDING_OUT],
        'maxFriends': friends_dynamo.MAX_FRIENDS,
    })
