"""
Monthly: propose the next N days of quizzes from the approved bank.

Assembly is separated from publishing on purpose. This creates drafts and stops;
a human publishes. Publishing is the irreversible step — it marks every question
used, so none can resurface on that calendar date in a later year — and it is
not something a schedule should do unattended.

The value is the buffer. Running monthly for sixty days ahead means there is
always at least a month of reviewed quizzes queued, so a bad week never becomes
a missing quiz.
"""

from datetime import date, datetime, timedelta, timezone

import boto3

from lambdas.common import constants, questions_dynamo, quizzes_dynamo
from lambdas.common.assembler import assemble
from lambdas.common.errors import handle_errors
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import success_response

log = get_logger(__file__)

HANDLER = 'cron_assemble_quizzes'

DEFAULT_DAYS_AHEAD = 60

_dynamo = None


def _runs_table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource('dynamodb')
    return _dynamo.Table(constants.SOURCE_RUNS_TABLE_NAME)


@handle_errors(HANDLER)
def handler(event, context):
    days = int((event or {}).get('daysAhead', DEFAULT_DAYS_AHEAD))
    start = datetime.now(timezone.utc).date()

    bank = questions_dynamo.list_bank('approved', limit=1000)
    log.info(f'assembling {days} days from a bank of {len(bank)}')

    proposed = skipped = incomplete = 0
    thin_dates = []

    for offset in range(days):
        quiz_date = (start + timedelta(days=offset)).isoformat()

        existing = quizzes_dynamo.get_quiz(quiz_date)
        if existing and existing.get('status') in ('published', 'scheduled'):
            # Never disturb a day a human has already signed off.
            skipped += 1
            continue

        used = quizzes_dynamo.used_question_ids(quiz_date[5:])
        result = assemble(quiz_date, bank, used_ids=used)
        item = result.to_item()

        try:
            quizzes_dynamo.put_draft(item)
        except ValueError as exc:
            log.warning(f'{quiz_date}: {exc}')
            skipped += 1
            continue

        proposed += 1
        if len(item['questionIds']) < constants.QUIZ_LENGTH:
            incomplete += 1
            thin_dates.append(quiz_date)

    _runs_table().put_item(Item={
        'runId': f"assemble-{date.today().isoformat()}",
        'source': 'assembler',
        'status': 'complete',
        'daysRequested': days,
        'proposed': proposed,
        'skipped': skipped,
        'incomplete': incomplete,
        # Surfaced rather than buried: a short day is a content gap that needs
        # more inventory, not something to quietly ship.
        'thinDates': thin_dates[:30],
        'bankSize': len(bank),
        'finishedAt': datetime.now(timezone.utc).isoformat(),
    })

    log.info(f'proposed {proposed}, skipped {skipped}, incomplete {incomplete}')
    return success_response({
        'proposed': proposed,
        'skipped': skipped,
        'incomplete': incomplete,
        'thinDates': thin_dates,
        'bankSize': len(bank),
    })
