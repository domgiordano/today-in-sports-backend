"""
GET /admin/review-queue - only the questions actually worth reviewing now.

The bank holds nearly nine thousand drafts. Reviewing them all is neither
possible nor useful: the vast majority are for dates months away, and many
belong to dates that already have enough approved questions.

What matters is a much smaller set — the next N days, and only the days still
short of a full quiz. That turns an unbounded chore into a finite one with an
end you can see: "August 14 needs two more".

Dates already carrying enough approved questions are skipped entirely, and
questions used on that calendar date in a previous year never appear, because
publishing moves them out of `approved`.
"""

from datetime import datetime, timedelta, timezone

from lambdas.common import questions_dynamo, quizzes_dynamo
from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'admin_review_queue'

DEFAULT_DAYS = 21
MAX_DAYS = 90
# Approved questions a date needs before it drops out of the queue. One spare
# beyond the five, so a late rejection does not empty the day.
TARGET_PER_DATE = 6
# Candidates offered per short date. More than this is wasted effort — the date
# only needs a handful.
CANDIDATES_PER_DATE = 12


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    days = max(1, min(int(params.get('days', DEFAULT_DAYS)), MAX_DAYS))
    # UTC, because the quiz day rolls over at midnight UTC. Local server time
    # would put the queue a day out for part of every day.
    start = datetime.now(timezone.utc).date()

    dates = []
    queue = []

    for offset in range(days):
        day = start + timedelta(days=offset)
        mmdd = day.strftime('%m-%d')

        approved, _ = questions_dynamo.list_by_status('approved', mmdd, limit=50)
        approved_count = len(approved)

        quiz = quizzes_dynamo.get_quiz(day.isoformat())
        status = (quiz or {}).get('status', 'none')

        needed = max(0, TARGET_PER_DATE - approved_count)
        entry = {
            'quizDate': day.isoformat(),
            'mmdd': mmdd,
            'approved': approved_count,
            'needed': needed,
            'quizStatus': status,
        }
        dates.append(entry)

        # A published day is settled; a day with enough approved needs nothing.
        if status == 'published' or needed == 0:
            continue

        drafts, _ = questions_dynamo.list_by_status('draft', mmdd,
                                                    limit=CANDIDATES_PER_DATE)
        for q in drafts:
            q['_forDate'] = day.isoformat()
            q['_needed'] = needed
        queue.extend(drafts)

    short_dates = [d for d in dates if d['needed'] > 0
                   and d['quizStatus'] != 'published']

    log.info(f'review queue: {len(queue)} candidates across '
             f'{len(short_dates)} short dates in {days} days')

    return success_response({
        'days': days,
        'questions': queue,
        'count': len(queue),
        'dates': dates,
        'shortDates': len(short_dates),
        'target': TARGET_PER_DATE,
    })
