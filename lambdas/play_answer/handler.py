"""
POST /play/answer - submit one answer, get it graded, get the next question.

The client posts a choice and nothing else. It does not post a score, it does
not post how long it took, and it never held the correct answer to begin with.
Everything that decides points is computed here from the stored session.

Replaying an index is rejected rather than re-graded, so a player cannot retry a
question they got wrong by resending it.
"""

from lambdas.common import identity as identity_mod
from lambdas.common import (
    badges,
    plays_dynamo,
    questions_dynamo,
    scoring,
    users_dynamo,
)
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, require_fields, success_response
from lambdas.common.play_view import options_for, public_question, today_utc

log = get_logger(__file__)

HANDLER = 'play_answer'


def _offers_second_chance(session, question, result, index):
    """
    Whether this miss earns a look at the options.

    Only for questions that hold options and were served without them, only
    when the player has not already taken the hint, and only once. Anything
    else would be a way to keep guessing.
    """
    if result['correct']:
        return False
    if question.get('type') != 'mc':
        return False
    if plays_dynamo.hint_used(session, index):
        return False
    if plays_dynamo.second_chance_used(session, index):
        return False
    return bool(options_for(question))


@handle_errors(HANDLER)
def handler(event, context):
    body = parse_body(event)
    require_fields(body, 'index')

    identity = (body.get('deviceId') or '').strip()
    # Verified from the bearer token: this route is public, so API Gateway
    # does not populate claims on it.
    claims = {'sub': identity_mod.subject(event)}
    signed_in = bool(claims.get('sub'))
    if signed_in:
        identity = claims['sub']

    if not identity:
        raise ValidationError(
            message='deviceId is required when not signed in',
            handler=HANDLER, function='handler')

    quiz_date = body.get('quizDate') or today_utc()
    index = int(body['index'])

    session = plays_dynamo.get_session(identity, quiz_date)
    if not session:
        raise NotFoundError(
            message='no play session; start the quiz first',
            handler=HANDLER, function='handler')

    if plays_dynamo.is_complete(session):
        raise ValidationError(
            message='this quiz is already complete',
            handler=HANDLER, function='handler')

    # A question already answered cannot be answered again — otherwise a wrong
    # answer is simply resubmitted until it is right.
    if plays_dynamo.already_answered(session, index):
        raise ValidationError(
            message=f'question {index} has already been answered',
            handler=HANDLER, function='handler')

    if index != int(session.get('currentIndex', 0)):
        raise ValidationError(
            message='out-of-order answer',
            handler=HANDLER, function='handler')

    question_ids = list(session.get('questionIds') or [])
    if index >= len(question_ids):
        raise ValidationError(
            message='index beyond the end of this quiz',
            handler=HANDLER, function='handler')

    question = questions_dynamo.get_question(question_ids[index])
    if not question:
        raise NotFoundError(
            message='question not found',
            handler=HANDLER, function='handler')

    # Server-stamped elapsed time, and a hint flag read from the session rather
    # than the request. The client is not consulted about anything that changes
    # the score.
    seconds = plays_dynamo.elapsed_since_served(session)
    used_hint = plays_dynamo.hint_used(session, index)
    taken_clues = plays_dynamo.clues_taken(session, index)
    result = scoring.grade(question, body.get('answer'), seconds, used_hint,
                           taken_clues)

    # A missed free response buys one look at the options, at the same price as
    # having asked for them outright. Recall is still worth more than
    # recognition — a right answer typed cold keeps full credit — but a miss is
    # no longer the end of the question. The clock is not reset and servedAt is
    # not cleared, so the time spent deciding still counts.
    if _offers_second_chance(session, question, result, index):
        plays_dynamo.record_second_chance(identity, quiz_date, index)
        # Charged through the same flag the hint uses, so seeing the options
        # costs the same however the player arrived at them.
        plays_dynamo.record_hint(identity, quiz_date, index)
        log.info(f'second chance granted on {quiz_date} index {index}')
        return success_response({
            'quizDate': quiz_date,
            'index': index,
            'retry': True,
            'options': options_for(question),
            'creditMultiplier': scoring.HINT_CREDIT,
            'seconds': result['seconds'],
            'state': 'playing',
        })

    session = plays_dynamo.record_answer(
        identity, quiz_date, index, body.get('answer'), result,
        sport=question.get('sport'))

    payload = {
        'quizDate': quiz_date,
        'index': index,
        'correct': result['correct'],
        'credit': result['credit'],
        'points': result['points'],
        'accuracyPoints': result['accuracyPoints'],
        'timeBonus': result['timeBonus'],
        'hintUsed': result['hintUsed'],
        'cluesTaken': result['cluesTaken'],
        'seconds': result['seconds'],
        'totalPoints': int(session.get('totalPoints', 0)),
        # Revealed only now that the answer is locked in.
        'correctAnswer': question.get('answer'),
        'sourceUrl': question.get('sourceDatasetRef'),
        # Where a map question's pin actually was, and what it is called. Held
        # back until this point for the same reason the coordinate is.
        'venueName': question.get('venueName'),
        'venuePlace': question.get('venuePlace'),
    }

    next_index = index + 1
    if next_index >= len(question_ids):
        session = plays_dynamo.complete_session(identity, quiz_date)
        payload['state'] = 'complete'
        payload['correctCount'] = int(session.get('correctCount', 0))
        payload['total'] = len(question_ids)

        # Streaks and badges belong to an account, not a device. An anonymous
        # player still plays, scores and appears on the day's board; what an
        # account buys is a history that survives clearing a browser. A badge a
        # cleared browser deletes is worse than no badge, and one that twenty
        # devices can farm is not an achievement.
        if signed_in:
            _award(identity, claims, quiz_date, session, question_ids, payload)

        log.info(f'{quiz_date} complete: {payload["totalPoints"]} points')
        return success_response(payload)

    next_question = questions_dynamo.get_question(question_ids[next_index])
    plays_dynamo.mark_served(identity, quiz_date, next_index)
    payload['state'] = 'playing'
    payload['question'] = public_question(next_question, next_index, len(question_ids))
    return success_response(payload)


def _award(identity, claims, quiz_date, session, question_ids, payload):
    """
    Fold a finished round into the player's history.

    Deliberately best-effort: a failure here must not turn a completed quiz
    into an error response. The player finished, the score is already stored
    and returned, and a missing badge is a far smaller problem than a round
    that appears to have failed.
    """
    try:
        users_dynamo.ensure_user(identity, claims.get('email'))

        served = [questions_dynamo.get_question(qid) for qid in question_ids]
        served = [q for q in served if q]

        user = users_dynamo.get_user(identity) or {}
        play_count = int(user.get('playCount') or 0) + 1
        streak = users_dynamo.next_streak(
            user.get('lastPlayedDate'), quiz_date, user.get('currentStreak'))

        awarded = badges.earned(session, served, streak, play_count)
        updated, fresh = users_dynamo.record_play(
            identity, quiz_date,
            int(session.get('totalPoints', 0)),
            int(session.get('correctCount', 0)),
            awarded)

        payload['streak'] = int(updated.get('currentStreak') or streak)
        payload['longestStreak'] = int(updated.get('longestStreak') or 0)
        payload['playCount'] = int(updated.get('playCount') or play_count)
        # Only the new ones, so the client can show the moment it happens
        # rather than listing a profile the player may never open.
        payload['newBadges'] = badges.describe(fresh)
    except Exception as exc:  # noqa: BLE001 - the round still counts
        log.warning(f'could not record play history (ignored): {exc}')
