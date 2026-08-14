"""
GET /admin/narrative - narrative candidates waiting on a person.

Params: status (`needs_review` by default), year, limit.

Each row carries the cited sentence and the link. The panel renders them side
by side with the question form, because the only thing that makes this source
safe is that whoever writes the question is looking at what it came from.
"""

from lambdas.common import narrative_dynamo
from lambdas.common.admin import require_admin
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.logger import get_logger
from lambdas.common.utility_helpers import get_query_params, success_response

log = get_logger(__file__)

HANDLER = 'admin_narrative'

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@handle_errors(HANDLER)
def handler(event, context):
    require_admin(event)
    params = get_query_params(event)

    status = params.get('status', 'needs_review')
    if status not in narrative_dynamo.CANDIDATE_STATUSES:
        raise ValidationError(
            message=(f"status must be one of "
                     f"{', '.join(narrative_dynamo.CANDIDATE_STATUSES)}"),
            handler=HANDLER,
            function='handler',
        )

    limit = max(1, min(int(params.get('limit', DEFAULT_LIMIT)), MAX_LIMIT))
    candidates = narrative_dynamo.list_candidates(
        status=status, limit=limit, year=params.get('year'))

    out = []
    for c in candidates:
        sentence = narrative_dynamo.cited_sentence(c)
        # A candidate with nothing to cite cannot produce a verifiable
        # question, so it is surfaced as unusable rather than offered for
        # someone to write around.
        out.append({
            'mmdd': c['mmdd'],
            'yearEventId': c['yearEventId'],
            'gameId': c.get('gameId'),
            'eventDate': c.get('gameDate'),
            'year': c.get('year'),
            'league': c.get('league'),
            'headline': (c.get('facts') or {}).get('headline') or c.get('title'),
            'citedSentence': sentence,
            'publishedAt': (c.get('facts') or {}).get('publishedAt'),
            'sourceName': c.get('sourceName'),
            'sourceDatasetRef': c.get('sourceDatasetRef'),
            'candidateScore': int(c.get('candidateScore') or 0),
            'usable': bool(sentence and c.get('sourceDatasetRef')),
            'status': c.get('status') or 'needs_review',
        })

    return success_response({
        'candidates': out,
        'count': len(out),
        'status': status,
        'writableTypes': list(narrative_dynamo.WRITABLE_TYPES),
    })
