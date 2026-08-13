#!/usr/bin/env python3
"""
Approve the questions that are clearly fine; leave the doubtful ones for a human.

    python scripts/auto_review.py --dry-run
    python scripts/auto_review.py --apply

Validation, which runs at generation time, answers "is this well-formed". This
answers a different and softer question: "would a person reading this think it
was a good question". A prompt can be perfectly well-formed and still give away
its own answer, or name a player as "Keefe", or ask something nobody could
possibly know.

Every rule here is arithmetic over the question's own fields. No model is asked
whether a question is good, for the same reason no model is asked what happened:
a plausible wrong judgement is undetectable at review time.

Anything that trips a rule stays `draft` and gains a `reviewFlags` list saying
why, so the review queue shows the reason rather than making someone rediscover
it. Nothing is ever auto-rejected - a flag means "a person should look", not
"this is wrong".
"""

import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3                                                    # noqa: E402
from boto3.dynamodb.conditions import Key                       # noqa: E402

from lambdas.common import constants                            # noqa: E402

# A prompt shorter than this is not a question, whatever it validates as.
MIN_PROMPT_CHARS = 25

# Numbers a sports question should never be asking for. Anything outside this
# is either a data error or a question nobody can answer.
MAX_PLAUSIBLE_COUNT = 1_000_000

# Sources that hand back today's franchise name whatever year is asked for.
#
# The MLB source resolves a club to the name it carried on the date, which is
# what yields "Brooklyn Robins" for a 1920 game. The basketball and hockey
# sources have no such lookup, so a 1956 game comes back as "Los Angeles Lakers
# routed the Atlanta Hawks" - a sentence in which both clubs are in the wrong
# city, the Lakers being in Minneapolis until 1960 and the Hawks in St. Louis
# until 1968. The question is confidently, checkably wrong.
#
# Until those sources learn franchise history, old questions from them are a
# human's call rather than a script's.
SPORTS_WITHOUT_HISTORICAL_NAMES = ("nba", "nhl")

# Before this, relocations are common enough that a modern name is a real risk.
RELOCATION_ERA_BEFORE = 1980


def _norm(text):
    return re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())


def _tokens(text):
    return [t for t in _norm(text).split() if len(t) > 2]


def flags_for(q):
    """Every reason a human should look at this question. Empty means approve."""
    problems = []

    prompt = q.get("prompt") or ""
    answer = q.get("answer")
    qtype = q.get("type")

    if len(prompt) < MIN_PROMPT_CHARS:
        problems.append("prompt is very short")

    # The answer sitting in its own prompt. Not caught by validation, because
    # the question is perfectly well-formed - it is just free.
    if qtype in ("mc", "clue") and isinstance(answer, str) and answer:
        if _norm(answer) and _norm(answer) in _norm(prompt):
            problems.append("answer appears in the prompt")

    # Retrosheet records some nineteenth-century players by surname alone. The
    # question is not wrong, but "Keefe" reads as a data gap rather than an
    # answer, and a person should decide whether to keep it.
    if qtype in ("mc", "clue") and isinstance(answer, str):
        if answer and len(answer.split()) < 2:
            problems.append("answer is a single-token name")

    if qtype == "mc":
        distractors = q.get("distractors") or []
        if len(set(map(_norm, distractors))) != len(distractors):
            problems.append("distractors repeat once normalised")
        # A distractor containing the answer, or vice versa, is solvable by
        # eye: "New York Yankees" against "Yankees".
        for d in distractors:
            if not _norm(d) or not _norm(answer):
                continue
            if _norm(d) in _norm(answer) or _norm(answer) in _norm(d):
                problems.append("a distractor overlaps the answer")
                break

    if qtype == "numeric":
        value = q.get("numericAnswer")
        if value is None:
            problems.append("no numeric answer")
        else:
            try:
                value = float(value)
                if value < 0:
                    problems.append("negative numeric answer")
                elif value > MAX_PLAUSIBLE_COUNT:
                    problems.append("implausibly large numeric answer")
            except (TypeError, ValueError):
                problems.append("numeric answer is not a number")

    if qtype == "ordering":
        items = q.get("items") or []
        # Near-identical labels make the ordering arbitrary rather than hard.
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                overlap = set(_tokens(a)) & set(_tokens(b))
                smaller = min(len(_tokens(a)), len(_tokens(b))) or 1
                if len(overlap) / smaller > 0.8:
                    problems.append("two items read almost identically")
                    break
            else:
                continue
            break

    if qtype == "clue":
        clues = q.get("clues") or []
        if isinstance(answer, str) and answer:
            for clue in clues:
                if _norm(answer) and _norm(answer) in _norm(clue):
                    problems.append("answer appears in a clue")
                    break

    # Negro Leagues questions are factually sound but carry framing decisions
    # that are not mine to make silently. Held for a person, deliberately.
    if q.get("isNegroLeagues"):
        problems.append("Negro Leagues - check the framing")

    if (q.get("sport") in SPORTS_WITHOUT_HISTORICAL_NAMES
            and int(q.get("year") or 0) < RELOCATION_ERA_BEFORE):
        problems.append("team name may be anachronistic - check the city")

    return problems


def scan_drafts(table):
    """Every draft question, paginated."""
    out, last_key = [], None
    while True:
        kwargs = {
            "IndexName": constants.QUESTIONS_STATUS_INDEX,
            "KeyConditionExpression": Key("status").eq("draft"),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        out.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the decisions")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="cap approvals, for a trial run")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb")
    table = dynamo.Table(constants.QUESTIONS_TABLE_NAME)

    drafts = scan_drafts(table)
    print(f"drafts: {len(drafts)}")

    approve, flagged = [], []
    reasons = collections.Counter()
    for q in drafts:
        problems = flags_for(q)
        if problems:
            flagged.append((q, problems))
            for p in problems:
                reasons[p] += 1
        else:
            approve.append(q)

    if args.limit:
        approve = approve[:args.limit]

    print(f"  auto-approve : {len(approve)}")
    print(f"  held for you : {len(flagged)}")
    print("\nwhy questions were held:")
    for reason, count in reasons.most_common():
        print(f"  {count:6d}  {reason}")

    print("\napprovals by type:",
          dict(collections.Counter(q["type"] for q in approve)))
    print("dates covered   :", len({q["mmdd"] for q in approve}), "/ 366")

    if not args.apply:
        print("\ndry run - nothing written")
        return

    written = 0
    for q in approve:
        table.update_item(
            Key={"questionId": q["questionId"]},
            UpdateExpression="SET #s = :s, reviewedBy = :who",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "approved", ":who": "auto-review"},
        )
        written += 1
        if written % 500 == 0:
            print(f"  approved {written}", flush=True)
    print(f"approved: {written}")

    # Flags are written so the review queue can say why a question is waiting
    # rather than making someone work it out again.
    for q, problems in flagged:
        table.update_item(
            Key={"questionId": q["questionId"]},
            UpdateExpression="SET reviewFlags = :f",
            ExpressionAttributeValues={":f": problems},
        )
    print(f"flagged for review: {len(flagged)}")


if __name__ == "__main__":
    main()
