"""
Narrative candidates: the human half of the news-archive rule.

Every other source in this corpus derives notability by rule and never needs a
person in the loop. Narrative events cannot work that way — no dataset has a
field meaning "was named the starter" — so the rule for this one source is:

    nothing may assert a fact; a question may only restate a sentence it was
    given, and that sentence is shown to the reviewer beside the question.

`scripts/ingest_news.py` does the first half: it writes Guardian articles into
the events table as `needs_review`, carrying the headline, the standfirst and
the link, with a date resolved from the article's own text or not at all.

This module is the second half. It lists those candidates, and it turns one
into a question — written by a person with the source open in front of them.

Two properties are enforced here rather than trusted:

  * **The cited sentence travels with the question.** It is copied onto the
    question row as `citedSentence`, so a reviewer six months later can check
    the claim without going back to the events table. A question without one
    is rejected.
  * **Provenance is immutable.** The URL comes from the candidate, never from
    the request body. Letting a caller supply it would make the citation a
    claim rather than a fact.

Questions written here land as `approved`, not `draft`. The review already
happened — a person read the sentence and typed the question — and sending it
back through a queue to be re-approved by the same person on the same day is
ceremony, not a check. `authoredBy` marks them so they are distinguishable
from a template's output.
"""

import hashlib
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

_dynamo = None

# The sport code `ingest_news.py` writes. Narrative candidates are not a sport
# in the way MLB is; the field is the corpus's routing key and this is the
# value that keeps them out of every template's input.
NARRATIVE_SPORT = "news"

# Candidate lifecycle. `needs_review` is what the ingest writes; the other two
# are terminal and mean a person has looked.
CANDIDATE_STATUSES = ("needs_review", "written", "discarded")

# Question formats a person can write from a sentence. Deliberately short:
# `ordering` and `map` need structured data the article does not have, and
# offering them would invite a hand-typed answer nothing can verify.
WRITABLE_TYPES = ("mc", "clue", "numeric")

MIN_DISTRACTORS = 3


def _events():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.EVENTS_TABLE_NAME)


def _questions():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.QUESTIONS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc).isoformat()


def list_candidates(status="needs_review", limit=50, year=None):
    """
    Narrative candidates awaiting a decision.

    Queried on the sport index rather than scanned: `sport` is its partition
    key, and every narrative candidate carries the same value, so the whole set
    is one partition. Status is filtered after the fact because it is not part
    of any key — the alternative is another GSI for a table whose narrative
    rows number in the hundreds.
    """
    cond = Key("sport").eq(NARRATIVE_SPORT)
    if year:
        cond = cond & Key("year").eq(int(year))

    out, last_key = [], None
    while True:
        kwargs = {
            "IndexName": constants.EVENTS_SPORT_INDEX,
            "KeyConditionExpression": cond,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = _events().query(**kwargs)
        out.extend(i for i in resp.get("Items", [])
                   if (i.get("status") or "needs_review") == status)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key or len(out) >= limit:
            break

    # Best first, then oldest.
    #
    # A queue this long is never finished, it is abandoned - so what matters is
    # what somebody sees in the first twenty minutes, not that the whole thing
    # is ordered. Chronological was the one ordering that said nothing at all
    # about whether a candidate was worth the time.
    out.sort(key=lambda i: (-int(i.get("candidateScore") or 0),
                            i.get("gameDate") or "",
                            i.get("gameId") or ""))
    return out[:limit]


def get_candidate(mmdd, year_event_id):
    resp = _events().get_item(Key={"mmdd": mmdd, "yearEventId": year_event_id})
    return resp.get("Item")


def set_candidate_status(mmdd, year_event_id, status, reviewer, note=None):
    """Record that a person decided about this candidate."""
    if status not in CANDIDATE_STATUSES:
        raise ValueError(f"invalid candidate status: {status}")

    expr = ["#s = :s", "reviewedAt = :t", "reviewedBy = :who"]
    values = {":s": status, ":t": _now(), ":who": reviewer}
    if note:
        expr.append("reviewNote = :n")
        values[":n"] = note

    resp = _events().update_item(
        Key={"mmdd": mmdd, "yearEventId": year_event_id},
        UpdateExpression="SET " + ", ".join(expr),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def cited_sentence(candidate):
    """
    The text the question must not go beyond.

    Headline and standfirst together, because a headline alone is often a pun
    and the standfirst is where the fact usually lives. This is the string a
    reviewer is shown and the string stored on the question; if it is empty the
    candidate cannot produce a verifiable question and is not offered.
    """
    facts = candidate.get("facts") or {}
    parts = [facts.get("headline") or candidate.get("title"),
             facts.get("summary")]
    return " — ".join(p.strip() for p in parts if p and p.strip())


def _tier_for(year):
    """
    Difficulty by recency, matching the template tiers.

    Narrative candidates only exist from 1999 on, so the older bands are
    unreachable here — that is a property of the source, not a special case.
    """
    year = int(year)
    if year >= 2020:
        return 1
    if year >= 2010:
        return 2
    if year >= 2000:
        return 3
    return 4


def validate(fields, candidate):
    """
    What a hand-written narrative question must satisfy.

    Returns a list of problems, empty when the question is well formed. The
    checks are the same shape as the template validators, plus the one rule
    specific to this source: the question must carry the sentence it came from.
    """
    problems = []

    qtype = fields.get("type")
    if qtype not in WRITABLE_TYPES:
        problems.append(
            f"type must be one of {', '.join(WRITABLE_TYPES)}")

    prompt = (fields.get("prompt") or "").strip()
    if len(prompt) < 15:
        problems.append("prompt is too short to be a question")

    answer = fields.get("answer")
    if qtype == "numeric":
        if not isinstance(answer, (int, float)):
            problems.append("a numeric question needs a numeric answer")
    else:
        if not (isinstance(answer, str) and answer.strip()):
            problems.append("missing answer")

    # The answer sitting inside its own prompt is the single most common way a
    # hand-written question is broken, and it is invisible while typing.
    if isinstance(answer, str) and answer.strip() and prompt:
        if answer.strip().lower() in prompt.lower():
            problems.append("the answer appears in the prompt")

    if qtype == "mc":
        distractors = [d for d in (fields.get("distractors") or [])
                       if str(d).strip()]
        if len(distractors) < MIN_DISTRACTORS:
            problems.append(
                f"a multiple-choice question needs {MIN_DISTRACTORS} distractors")
        if isinstance(answer, str) and any(
                str(d).strip().lower() == answer.strip().lower()
                for d in distractors):
            problems.append("a distractor repeats the answer")
        if len({str(d).strip().lower() for d in distractors}) != len(distractors):
            problems.append("duplicate distractors")

    if qtype == "clue":
        clues = [c for c in (fields.get("clues") or []) if str(c).strip()]
        if len(clues) < 3:
            problems.append("a clue ladder needs at least three clues")
        if isinstance(answer, str) and answer.strip() and any(
                answer.strip().lower() in str(c).lower() for c in clues):
            problems.append("the answer appears in its own clues")

    if not cited_sentence(candidate):
        problems.append("the candidate carries no sentence to cite")
    if not candidate.get("sourceDatasetRef"):
        problems.append("the candidate carries no source link")

    return problems


def question_from_candidate(candidate, fields, author):
    """
    Write a question from a candidate, and record what it was written from.

    Provenance comes from the candidate and never from `fields`: the caller
    supplies the wording, the corpus supplies the citation. That split is the
    whole reason this source is safe to use.
    """
    problems = validate(fields, candidate)
    if problems:
        raise ValueError("; ".join(problems))

    qtype = fields["type"]
    sentence = cited_sentence(candidate)

    question_id = hashlib.sha1(
        f"{candidate['gameId']}|{qtype}|{fields['prompt']}".encode()
    ).hexdigest()[:16]

    tier = int(fields.get("tier") or _tier_for(candidate["year"]))
    item = {
        "questionId": question_id,
        "type": qtype,
        "tier": max(1, min(tier, 5)),
        "prompt": fields["prompt"].strip(),
        "answer": (fields["answer"].strip()
                   if isinstance(fields["answer"], str) else fields["answer"]),
        "sport": NARRATIVE_SPORT,
        "league": candidate.get("league") or "Sport",
        "isNegroLeagues": False,
        "mmdd": candidate["mmdd"],
        "year": int(candidate["year"]),
        "sportTier": f"{NARRATIVE_SPORT}#{max(1, min(tier, 5))}",

        "sourceEventId": candidate["gameId"],
        "sourceReason": candidate.get("reason") or "narrative_event",
        "sourceName": candidate["sourceName"],
        "sourceDatasetRef": candidate["sourceDatasetRef"],

        # The claim, verbatim, travelling with the question. Everything about
        # this source rests on this field being present and unedited.
        "citedSentence": sentence,

        "status": "approved",
        "authoredBy": author,
        "authoredAt": _now(),
    }

    if qtype == "mc":
        item["distractors"] = [str(d).strip()
                               for d in fields["distractors"] if str(d).strip()]
    if qtype == "clue":
        clues = [str(c).strip() for c in fields["clues"] if str(c).strip()]
        item["clues"] = clues
        item["clueCount"] = len(clues)
    if qtype == "numeric":
        item["numericAnswer"] = fields["answer"]
        if fields.get("tolerance") is not None:
            item["tolerance"] = fields["tolerance"]

    _questions().put_item(Item=item)
    log.info(f"narrative question {question_id} written by {author} "
             f"from {candidate['sourceDatasetRef']}")
    return item
