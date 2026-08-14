#!/usr/bin/env python3
"""
Reassemble quizzes whose questions no longer exist.

    python scripts/repair_quizzes.py --dry-run
    python scripts/repair_quizzes.py --apply

A question id hashes its own answer, so correcting an answer changes the id:
"Keefe" becoming "Tim Keefe" retires one row and creates another. That is the
right behaviour - the old question really is gone - but a quiz holding the old
id then resolves to nothing, and a published quiz that resolves to nothing is
the worst outcome available.

`load_corpus.py` no longer prunes anything a quiz references, so this should
not recur. This repairs the nine that were already broken when that guard was
added.

Only the missing slots are refilled. A quiz that still resolves is left exactly
as it is, because a published quiz somebody may already have played should not
silently become a different quiz.
"""

import argparse
import collections
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3                                                     # noqa: E402

from lambdas.common import assembler, constants                  # noqa: E402


def _scan(table, **kwargs):
    last_key, out = None, []
    while True:
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        out.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb")
    quizzes_table = dynamo.Table(constants.QUIZZES_TABLE_NAME)
    questions_table = dynamo.Table(constants.QUESTIONS_TABLE_NAME)

    questions = _scan(questions_table)
    by_id = {q["questionId"]: q for q in questions}
    approved = [q for q in questions if q.get("status") in ("approved", "used")]
    quizzes = _scan(quizzes_table)

    # Every question already spoken for on this calendar date, so a repair
    # cannot hand one day's quiz a question another day is holding.
    spoken_for = {qid for z in quizzes for qid in (z.get("questionIds") or [])}

    repaired = 0
    for quiz in sorted(quizzes, key=lambda z: z["quizDate"]):
        ids = quiz.get("questionIds") or []
        missing = [q for q in ids if q not in by_id]
        if not missing:
            continue

        mmdd = quiz["quizDate"][5:]
        # Same calendar date, approved, and not already used somewhere else.
        pool = [q for q in approved
                if q["mmdd"] == mmdd and q["questionId"] not in spoken_for]
        # Fill each gap the way the assembler would have: strongest candidate,
        # preferring a sport and a format the surviving questions do not
        # already cover.
        kept = [by_id[q] for q in ids if q in by_id]
        chosen_sports = {q["sport"] for q in kept}
        chosen_types = collections.Counter(q.get("type") for q in kept)

        replacements = []
        available = list(pool)
        for _ in missing:
            pick = assembler._best(available, chosen_sports, chosen_types)
            if not pick:
                break
            replacements.append(pick)
            available.remove(pick)
            chosen_sports.add(pick["sport"])
            chosen_types[pick.get("type")] += 1

        if len(replacements) < len(missing):
            print(f"  {quiz['quizDate']}  cannot refill "
                  f"({len(missing)} missing, only {len(replacements)} in bank)")
            continue

        queue = list(replacements)
        new_ids = [q if q in by_id else queue.pop(0)["questionId"] for q in ids]

        print(f"  {quiz['quizDate']}  {quiz.get('status'):9s} "
              f"{len(missing)} replaced")
        spoken_for.update(new_ids)
        repaired += 1

        if args.apply:
            quizzes_table.update_item(
                Key={"quizDate": quiz["quizDate"]},
                UpdateExpression="SET questionIds = :q, repairedAt = :t",
                ExpressionAttributeValues={
                    ":q": new_ids,
                    ":t": datetime.now(timezone.utc).isoformat(),
                },
            )

    print(f"\n{'repaired' if args.apply else 'would repair'}: {repaired}")
    if not args.apply:
        print("dry run - nothing written")


if __name__ == "__main__":
    main()
