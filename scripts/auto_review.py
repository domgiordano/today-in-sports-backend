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
# what yields "Brooklyn Robins" for a 1920 game. Hockey now does too: the NHL
# game log carries an era-specific tricode, so MNS resolves to the Minnesota
# North Stars and DAL to the Dallas Stars without needing to know a relocation
# date at all.
#
# Basketball still cannot. balldontlie returns the modern franchise name *and*
# the modern code for a 1953 game, so there is no signal in the data saying
# which era it belongs to - "the Atlanta Hawks and Sacramento Kings met on
# January 20, 1953" names two cities neither club had reached, and nothing in
# the payload could have said otherwise. Those stay a human's call until
# there is a source that knows.
SPORTS_WITHOUT_HISTORICAL_NAMES = ("nba",)

# Before this, a modern franchise name may not be the name the club carried.
#
# This was 1980, on the assumption that relocations were a mid-century thing.
# They are not: Seattle became Oklahoma City in 2008, New Jersey became
# Brooklyn in 2012, Charlotte took its name back in 2014. 185 events named a
# club under a name it did not yet have, and every one of them was after 1980
# and so sailed straight past the flag - "the Oklahoma City Thunder routed the
# LA Clippers" on a game played in 1988.
#
# The cutoff sits after the last identity change anyone has made, because a
# year cutoff is the only rule available while the source gives no era signal
# at all. It is blunt and it costs most of the basketball inventory to review
# rather than auto-approval, which is the right side to be wrong on.
RELOCATION_ERA_BEFORE = 2015


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


def iter_status(table, status):
    """Every question in one status, a page at a time."""
    last_key = None
    while True:
        kwargs = {
            "IndexName": constants.QUESTIONS_STATUS_INDEX,
            "KeyConditionExpression": Key("status").eq(status),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            yield item
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return


def recheck_approved(table, apply_changes):
    """
    Re-run the rules over already-approved questions.

    Every rule here was added after some questions had already been approved,
    and this script only ever looked at drafts - so a new rule silently applied
    to future questions and never to the ones already through. The relocation
    cutoff moving from 1980 to 2015 left 199 approved questions naming clubs in
    cities they had not reached, and nothing would have looked at them again.

    Demotes rather than rejects: failing a rule means "a person should look",
    which is what draft plus a flag says.
    """
    demoted = 0
    for q in iter_status(table, "approved"):
        # Hand-written questions have no generator and no rule to re-apply;
        # a person already decided about them with the source in front of them.
        if q.get("authoredBy"):
            continue
        problems = flags_for(q)
        if not problems:
            continue
        demoted += 1
        if apply_changes:
            table.update_item(
                Key={"questionId": q["questionId"]},
                UpdateExpression=("SET #s = :d, reviewFlags = :f, "
                                  "reviewedBy = :who REMOVE reviewedAt"),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":d": "draft", ":f": problems, ":who": "auto-review-recheck"},
            )
    return demoted


def iter_drafts(table):
    """
    Every draft question, a page at a time.

    Deliberately a generator. The first version built one list of every draft
    and the process was killed by the OOM reaper partway through a 24,000-row
    bank - leaving 5,468 questions approved and the rest untouched, which is
    the worst possible place to stop. Nothing here needs the whole set at once:
    each question is judged on its own fields.
    """
    last_key = None
    while True:
        kwargs = {
            "IndexName": constants.QUESTIONS_STATUS_INDEX,
            "KeyConditionExpression": Key("status").eq("draft"),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            yield item
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the decisions")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="cap approvals, for a trial run")
    ap.add_argument("--recheck", action="store_true",
                    help="re-run the rules over approved questions and demote "
                         "any that now fail")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb")
    table = dynamo.Table(constants.QUESTIONS_TABLE_NAME)

    # Approved first, so a question demoted by a rule change is reconsidered in
    # the same run rather than sitting approved until somebody notices.
    if args.recheck:
        demoted = recheck_approved(table, args.apply)
        print(f"approved questions now failing the rules: {demoted}"
              + ("" if args.apply else " (not written)"))

    # Decisions are made and written in one pass over the stream, so an
    # interrupted run leaves a partially-reviewed bank rather than a half-built
    # list and nothing written at all.
    approved = flagged = seen = 0
    reasons = collections.Counter()
    types = collections.Counter()
    dates = set()
    pending_flags = []

    for q in iter_drafts(table):
        seen += 1
        problems = flags_for(q)

        if problems:
            flagged += 1
            for p in problems:
                reasons[p] += 1
            pending_flags.append((q["questionId"], problems))
        else:
            if args.limit and approved >= args.limit:
                continue
            approved += 1
            types[q["type"]] += 1
            dates.add(q["mmdd"])
            if args.apply:
                table.update_item(
                    Key={"questionId": q["questionId"]},
                    UpdateExpression="SET #s = :s, reviewedBy = :who",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":s": "approved",
                                               ":who": "auto-review"},
                )
                if approved % 1000 == 0:
                    print(f"  approved {approved}", flush=True)

        if seen % 5000 == 0:
            print(f"  scanned {seen}", flush=True)

    print(f"drafts scanned : {seen}")
    print(f"  auto-approve : {approved}")
    print(f"  held for you : {flagged}")
    print("\nwhy questions were held:")
    for reason, count in reasons.most_common():
        print(f"  {count:6d}  {reason}")

    print("\napprovals by type:", dict(types))
    print("dates covered   :", len(dates), "/ 366")

    if not args.apply:
        print("\ndry run - nothing written")
        return

    # Flags are written so the review queue can say why a question is waiting
    # rather than making someone work it out again.
    for question_id, problems in pending_flags:
        table.update_item(
            Key={"questionId": question_id},
            UpdateExpression="SET reviewFlags = :f",
            ExpressionAttributeValues={":f": problems},
        )
    print(f"approved: {approved}")
    print(f"flagged for review: {flagged}")


if __name__ == "__main__":
    main()
