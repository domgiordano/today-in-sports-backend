"""
GET /account/groups - the groups this player belongs to, and how each is doing.

Only the caller's own groups. There is deliberately no endpoint answering
"what groups exist": a searchable directory of small private groups is a
harassment surface with no upside for a trivia game.

Each group carries its rolled-up numbers, which is why group analytics live
here rather than on the public stats route. Membership is the permission, and
this is the only endpoint that already knows it.
"""

from lambdas.common import (groups_dynamo, plays_dynamo, stats_dynamo,
                            usernames_dynamo, users_dynamo)
from lambdas.common.play_view import today_utc
from lambdas.common.errors import handle_errors, UnauthorizedError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'account_groups'


def caller(event):
    claims = ((event.get('requestContext') or {}).get('authorizer') or {})
    claims = claims.get('claims') or claims
    user_id = claims.get('sub')
    if not user_id:
        raise UnauthorizedError(
            message='sign in to use groups',
            handler=HANDLER, function='caller')
    return user_id, claims


@handle_errors(HANDLER)
def handler(event, context):
    user_id, claims = caller(event)
    user = users_dynamo.ensure_user(user_id, claims.get('email'))

    groups = groups_dynamo.groups_for(user.get('groupIds') or set())
    quiz_date = today_utc()

    return success_response({
        # The code travels because the caller is already a member of every
        # group in this list.
        'groups': [_with_stats(g, quiz_date) for g in groups],
        'maxMembers': groups_dynamo.MAX_MEMBERS,
    })


# What a group screen may show. Deliberately the same fields the public page
# shows for everyone, so a group is a comparison and not a different report.
STAT_FIELDS = (
    'rounds', 'players', 'avgPoints', 'avgCorrect',
    'perfectRounds', 'avgSeconds', 'bestPoints', 'bySport',
)


def _standings(group, quiz_date):
    """
    The table: everybody in the group, ranked, and whether they have played
    today.

    A group used to report only its own averages — you could see that the group
    scored 640 on average and not who was in it, which is a statistic rather
    than a competition. Every figure here is already on the member profiles, so
    this is two batch reads and no aggregation.

    Ranked on total points because that is what accumulates. A daily board
    resets every morning; a season table is the thing worth coming back to.
    """
    member_ids = sorted(group.get('memberIds') or set())
    if not member_ids:
        return []

    profiles = users_dynamo.profiles(member_ids)
    today = {s.get('identity'): s
             for s in plays_dynamo.sessions_for(member_ids, quiz_date)}

    rows = []
    for user_id in member_ids:
        profile = profiles.get(user_id) or {}
        session = today.get(user_id) or {}
        rows.append({
            'userId': user_id,
            'displayName': profile.get('displayName') or 'Unnamed player',
            'username': usernames_dynamo.current_for(user_id),
            'isOwner': user_id == group.get('ownerId'),
            'totalPoints': int(profile.get('totalPoints') or 0),
            'playCount': int(profile.get('playCount') or 0),
            'totalCorrect': int(profile.get('totalCorrect') or 0),
            'currentStreak': int(profile.get('currentStreak') or 0),
            'longestStreak': int(profile.get('longestStreak') or 0),
            'lastPlayedDate': profile.get('lastPlayedDate'),
            # None, not zero: "has not played yet" and "played and scored
            # nothing" are different things and the table should not conflate
            # them at nine in the morning.
            'todayPoints': (int(session['totalPoints'])
                            if session.get('completedAt') else None),
            'todayCorrect': (int(session.get('correctCount') or 0)
                             if session.get('completedAt') else None),
            'playedToday': bool(session.get('completedAt')),
        })

    rows.sort(key=lambda r: (-r['totalPoints'], -r['playCount'],
                             r['displayName'].lower()))
    for position, row in enumerate(rows, start=1):
        row['position'] = position
    return rows


def _with_stats(group, quiz_date):
    view = groups_dynamo.public_view(group, include_code=True)
    rollup = stats_dynamo.get_rollup(f"group#{group['groupId']}", 'all')
    # None rather than zeroes: a group that has not played yet has no numbers,
    # and zeroes would read as a real and very bad result.
    view['stats'] = (
        {field: rollup.get(field) for field in STAT_FIELDS} if rollup else None)
    view['members'] = _standings(group, quiz_date)
    return view
