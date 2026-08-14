"""
POST /admin/narrative/{mmdd}/{yearEventId} - act on a narrative candidate.

Body: {"action": "write", "fields": {...}} or {"action": "discard", "note": ...}

Writing produces an `approved` question carrying the candidate's cited sentence
and link. Discarding marks the candidate so it never comes back round — most
articles are not worth a question, and a queue that re-offers them is a queue
nobody finishes.

The request supplies wording only. The citation is read from the candidate, so
a caller cannot point a question at a source it did not come from.
"""

from lambdas.common import narrative_dynamo
from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors, NotFoundError, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import (
    get_path_params,
    parse_body,
    require_fields,
    success_response,
)

log = get_logger(__file__)

HANDLER = 'admin_narrative_write'


@handle_errors(HANDLER)
def handler(event, context):
    author = require_admin(event)
    body = parse_body(event)
    require_fields(body, 'action')

    path = get_path_params(event)
    mmdd = path.get('mmdd') or body.get('mmdd')
    year_event_id = path.get('yearEventId') or body.get('yearEventId')
    if not (mmdd and year_event_id):
        raise ValidationError(
            message='mmdd and yearEventId are both required',
            handler=HANDLER,
            function='handler',
        )

    candidate = narrative_dynamo.get_candidate(mmdd, year_event_id)
    if not candidate:
        raise NotFoundError(
            message=f'no narrative candidate {mmdd}/{year_event_id}',
            handler=HANDLER,
            function='handler',
        )

    action = body['action']

    if action == 'discard':
        item = narrative_dynamo.set_candidate_status(
            mmdd, year_event_id, 'discarded', author, body.get('note'))
        return success_response({'candidate': item})

    if action != 'write':
        raise ValidationError(
            message=f"unknown action '{action}'",
            handler=HANDLER,
            function='handler',
        )

    fields = body.get('fields') or {}
    try:
        question = narrative_dynamo.question_from_candidate(
            candidate, fields, author)
    except ValueError as exc:
        # Validation failures are the author's typos, not a server fault, and
        # the panel shows the message verbatim beside the form.
        raise ValidationError(
            message=str(exc),
            handler=HANDLER,
            function='handler',
        ) from exc

    # Only marked written once the question exists. A candidate marked first
    # and failing validation second would vanish from the queue with nothing
    # to show for it.
    candidate = narrative_dynamo.set_candidate_status(
        mmdd, year_event_id, 'written', author)

    return success_response({
        'question': question,
        'candidate': candidate,
    })
