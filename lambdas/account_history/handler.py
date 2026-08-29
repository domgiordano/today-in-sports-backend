"""
GET /account/history - your own rounds, and what they say about you.

The public numbers already say how everybody does. This is the half a player
actually came for: their run of days, their accuracy by sport, and the same
figures the global page shows so the two can sit side by side.

Nothing here is precomputed. The window is one BatchGetItem on keys derived
from the player's own id, so this costs the same whether the game has ten
players or ten thousand.
"""

from datetime import date

from lambdas.common import group_access, plays_dynamo
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.play_view import today_utc
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'account_history'

DEFAULT_DAYS = 30
# Sessions carry a 90-day TTL (plays_dynamo.SESSION_TTL_DAYS), so a longer
# window cannot return anything and would only cost reads finding that out.
MAX_DAYS = 90


def _round(session):
    answers = session.get('answers') or []
    seconds = [float(a['seconds']) for a in answers if a.get('seconds')]
    return {
        'quizDate': session.get('quizDate'),
        'points': int(session.get('totalPoints') or 0),
        'correct': int(session.get('correctCount') or 0),
        'total': len(session.get('questionIds') or answers),
        # Total time over the round, not per answer: a player comparing two
        # days wants "did I take longer today", and a mean of five hides it.
        'seconds': round(sum(seconds)) if seconds else None,
    }


def _by_sport(sessions):
    """
    Accuracy per sport across the window.

    Answers recorded before `sport` was stored carry none. Those are left out
    rather than bucketed as unknown, which would invent a sport nobody played.
    """
    tally = {}
    for session in sessions:
        for answer in session.get('answers') or []:
            sport = answer.get('sport')
            if not sport:
                continue
            row = tally.setdefault(sport, {'asked': 0, 'correct': 0})
            row['asked'] += 1
            row['correct'] += 1 if answer.get('correct') else 0

    return {
        sport: {**row, 'accuracy': round(row['correct'] / row['asked'], 3)}
        for sport, row in tally.items() if row['asked']
    }


@handle_errors(HANDLER)
def handler(event, context):
    user_id = group_access.caller(event, HANDLER)
    params = get_query_params(event)

    try:
        days = int(params.get('days') or DEFAULT_DAYS)
    except ValueError:
        days = DEFAULT_DAYS
    days = max(1, min(days, MAX_DAYS))

    today = today_utc()
    sessions = plays_dynamo.history(user_id, days, date.fromisoformat(today))
    rounds = [_round(s) for s in sessions]

    played = [r for r in rounds if r['total']]
    points = [r['points'] for r in played]
    correct = [r['correct'] for r in played]

    return success_response({
        'days': days,
        'through': today,
        'rounds': rounds,
        'bySport': _by_sport(sessions),
        # Summarised over the window rather than over all time, so it answers
        # "how am I playing lately" rather than repeating the lifetime totals
        # that /me already carries.
        'window': {
            'roundsPlayed': len(played),
            'avgPoints': round(sum(points) / len(points)) if points else 0,
            'avgCorrect': round(sum(correct) / len(correct), 1) if correct else 0,
            'bestPoints': max(points) if points else 0,
            'perfectRounds': sum(1 for r in played if r['correct'] == r['total']),
        },
    })
