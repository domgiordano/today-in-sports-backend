"""
Daily: publish assembled drafts, so the game does not go dark.

Assembly was separated from publishing on the reasoning that publishing is
irreversible - it marks every question used, so none can resurface on that
calendar date in a later year - and that a schedule should not do that
unattended.

The reasoning held; the arithmetic did not. Assembly is monthly and automatic,
publishing was manual and therefore intermittent, and `play_start` refuses
anything unpublished. So the published run always ended before the assembled
one, and the morning after it ended every player got "no published quiz"
instead of a game. Sixty days were assembled and thirty published, with nothing
anywhere saying so.

What the manual gate was actually protecting is already protected earlier and
better. Every question in a quiz is `approved`, which a person decided. The
assembler enforces the sport mix, the tier ladder and the no-repeat rule. The
gate re-asked a question review had already answered, and its failure mode was
total.

So this publishes, and the checks that matter run here rather than in
somebody's memory:

  * exactly five questions, and every one of them resolvable. A quiz that
    resolves to nothing is worse than a missing quiz, because it fails at the
    player rather than at the schedule.
  * every question still `approved`. A question rejected after assembly must
    not ship because a draft quiz remembered it.
  * today is never touched. If today is somehow unpublished, publishing it now
    would change the quiz under anybody already playing.
"""

from datetime import date, datetime, timedelta, timezone

import boto3

from lambdas.common import constants, questions_dynamo, quizzes_dynamo
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'cron_publish_quizzes'

# How far ahead to publish. Long enough that a failed run or two is invisible,
# short enough that a question rejected next week still gets caught before the
# quiz holding it ships.
DEFAULT_HORIZON_DAYS = 45

# Publishing changes the quiz a player is being served, so the current day is
# left exactly as it is.
FIRST_PUBLISHABLE_OFFSET = 1

_dynamo = None


def _runs_table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource('dynamodb')
    return _dynamo.Table(constants.SOURCE_RUNS_TABLE_NAME)


def publishable(quiz, questions_by_id):
    """
    Why this quiz may or may not be published, as (ok, reason).

    Returns the reason rather than a bare False so a skipped day says what is
    wrong with it. A day silently declined every morning is a day nobody fixes.
    """
    ids = quiz.get('questionIds') or []

    if len(ids) != constants.QUIZ_LENGTH:
        return False, f'{len(ids)} questions, expected {constants.QUIZ_LENGTH}'

    missing = [q for q in ids if q not in questions_by_id]
    if missing:
        return False, f'{len(missing)} questions no longer exist'

    not_approved = [q for q in ids
                    if questions_by_id[q].get('status') not in
                    ('approved', 'used')]
    if not_approved:
        return False, f'{len(not_approved)} questions are not approved'

    if len(set(ids)) != len(ids):
        return False, 'the same question appears twice'

    return True, ''


@handle_errors(HANDLER)
def handler(event, context):
    horizon = int((event or {}).get('horizonDays', DEFAULT_HORIZON_DAYS))
    dry_run = bool((event or {}).get('dryRun'))
    today = datetime.now(timezone.utc).date()

    # The candidate days first, so only the questions they actually reference
    # are read - a couple of hundred rather than the whole bank.
    candidates = []
    for offset in range(FIRST_PUBLISHABLE_OFFSET, horizon + 1):
        quiz_date = (today + timedelta(days=offset)).isoformat()
        quiz = quizzes_dynamo.get_quiz(quiz_date)
        # `held` is a veto somebody entered deliberately. Treating it as just
        # another unpublished day would republish it the next morning, which
        # is the whole thing the status exists to prevent.
        if quiz and quiz.get('status') in ('draft', 'scheduled'):
            candidates.append(quiz)

    questions_by_id = questions_dynamo.get_many(
        [q for quiz in candidates for q in (quiz.get('questionIds') or [])])

    published, skipped = [], []
    for quiz in candidates:
        quiz_date = quiz['quizDate']
        ok, reason = publishable(quiz, questions_by_id)
        if not ok:
            log.warning(f'not publishing {quiz_date}: {reason}')
            skipped.append({'quizDate': quiz_date, 'reason': reason})
            continue

        if not dry_run:
            quizzes_dynamo.set_status(quiz_date, 'published')
        published.append(quiz_date)

    runway = quizzes_dynamo.published_runway()
    log.info(f'published {len(published)}, skipped {len(skipped)}, '
             f'runway now {runway["runwayDays"]} days')

    # A day that cannot be published is the only thing here worth anybody's
    # attention, so it is recorded rather than only logged.
    if not dry_run:
        _runs_table().put_item(Item={
            'runId': f'publish-{today.isoformat()}',
            'source': 'publisher',
            'startedAt': datetime.now(timezone.utc).isoformat(),
            'status': 'complete',
            'publishedCount': len(published),
            'skipped': skipped,
            'runwayDays': runway['runwayDays'],
        })

    return success_response({
        'published': published,
        'publishedCount': len(published),
        'skipped': skipped,
        'runway': runway,
        'dryRun': dry_run,
    })
