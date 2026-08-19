"""
POST /admin/quizzes/assemble - propose quizzes from the approved bank.

Body: {"startDate": "2026-08-13", "days": 30}

Proposals are drafts. Nothing reaches players until a human publishes it, and
the assembler reports every constraint it had to relax rather than hiding a
thin day behind a complete-looking quiz.
"""

from datetime import date, timedelta

from lambdas.common.admin import require_admin
from lambdas.common import assembler
from lambdas.common.assembler import assemble
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import parse_body, success_response
from lambdas.common import constants, questions_dynamo, quizzes_dynamo

log = get_logger(__file__)

HANDLER = 'admin_quizzes_assemble'


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    body = parse_body(event)

    start = body.get('startDate') or date.today().isoformat()
    days = int(body.get('days', constants.DEFAULT_ASSEMBLE_DAYS))
    if days < 1 or days > constants.MAX_ASSEMBLE_DAYS:
        raise ValidationError(
            message=f'days must be between 1 and {constants.MAX_ASSEMBLE_DAYS}',
            handler=HANDLER,
            function='handler',
        )

    try:
        start_date = date.fromisoformat(start)
    except ValueError:
        raise ValidationError(
            message=f"startDate '{start}' is not a yyyy-mm-dd date",
            handler=HANDLER,
            function='handler',
        )

    # Rebuilding days that are already published.
    #
    # Normally a published day is untouchable: somebody signed it off and
    # players may be part-way through it. But these days are published by
    # `cron_publish_quizzes` without a human in the loop, so a change to the
    # assembly rules would otherwise take as long as the publish runway to
    # reach anybody — six weeks of the old quiz while the fix sits in the
    # repository.
    #
    # Strictly future dates only, and only when asked for explicitly. Today is
    # excluded on purpose: people are part-way through it, and a quiz that
    # changes under a player mid-run is worse than a repetitive one.
    rebuild_published = bool(body.get('rebuildPublished'))

    proposed, skipped, rebuilt = [], [], []
    openers = []
    for offset in range(days):
        d = (start_date + timedelta(days=offset)).isoformat()

        existing = quizzes_dynamo.get_quiz(d)
        if existing and existing.get('status') == 'published':
            if not (rebuild_published and d > date.today().isoformat()):
                skipped.append({'quizDate': d, 'reason': 'already published'})
                continue
            rebuilt.append(d)

        # Fetched per date, not sliced off a global bank — the same fix the
        # cron handler already carries. A flat 1,000-row slice of a 25,000
        # question bank holds almost nothing for any particular calendar day,
        # so every quiz came out thin and single-sported for no visible reason.
        bank, _ = questions_dynamo.list_by_status('approved', d[5:], limit=200)
        used = quizzes_dynamo.used_question_ids(d[5:])
        result = assemble(d, bank, used_ids=used, recent_openers=openers)
        # See the cron handler: the rotation only works if it is carried.
        assembler.remember_opener(openers, result.questions)
        item = result.to_item()
        # A rebuilt day goes back out published rather than dropping to draft,
        # so replacing it does not leave a hole in the runway. put_draft
        # refuses to touch a published day and should keep refusing; this is
        # the one path allowed to, and it checks the date itself.
        if d in rebuilt:
            quizzes_dynamo.replace_published(item)
        else:
            quizzes_dynamo.put_draft(item)
        proposed.append(item)

    incomplete = [p for p in proposed if len(p['questionIds']) < constants.QUIZ_LENGTH]
    log.info(f"assembled {len(proposed)} quizzes, {len(incomplete)} incomplete")

    return success_response({
        'rebuilt': rebuilt,
        'proposed': proposed,
        'skipped': skipped,
        'incompleteCount': len(incomplete),
        'bankSize': len(bank),
    })
