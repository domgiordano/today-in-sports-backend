#!/usr/bin/env python3
"""
Delete questions whose wording a template has since replaced.

    python scripts/prune_superseded.py --dry-run
    python scripts/prune_superseded.py --apply

A question's id hashes its own prompt, so a template change does not edit a
question — it mints a new one and leaves the old one behind, still approved and
still eligible. Regenerating without pruning therefore fixes nothing a player
sees: both wordings sit in the bank and the assembler picks whichever scores
better, which is usually the older one because there are more of them.

Superseded means: the same event and format now produce a different id. That is
the template having changed what it says about a fact, not a new fact.

Deletion is last and it is ordered, for the reason `rekey_questions` gives —
run it before the quizzes have moved and published days resolve to nothing. A
row still referenced by any quiz is kept and reported, never deleted.
"""

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3                                                     # noqa: E402

from decimal import Decimal                                       # noqa: E402

from lambdas.common import constants                             # noqa: E402
from lambdas.common import regeneration                          # noqa: E402

def _plain(o):
    """Decimal back to int/float so template arithmetic behaves."""
    if isinstance(o, list):
        return [_plain(v) for v in o]
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    return o


def scan(table, **kw):
    out, page_kw = [], dict(kw)
    while True:
        page = table.scan(**page_kw)
        out.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return out
        page_kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="delete the unreferenced superseded rows")
    ap.add_argument("--retire", action="store_true",
                    help="mark every superseded row rejected, referenced or not")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb")
    qt = dynamo.Table(constants.QUESTIONS_TABLE_NAME)
    zt = dynamo.Table(constants.QUIZZES_TABLE_NAME)

    questions = scan(qt)
    quizzes = scan(zt)
    referenced = {qid for z in quizzes for qid in (z.get("questionIds") or [])}
    print(f"questions: {len(questions)}   referenced by a quiz: {len(referenced)}")

    events = scan(dynamo.Table(constants.EVENTS_TABLE_NAME))
    events = [_plain(e) for e in events]
    fresh = regeneration.regenerate(events)
    fresh_ids = {q["questionId"] for q in fresh}
    regenerated_slots = regeneration.slots(fresh)
    print(f"ids the templates produce today: {len(fresh_ids)}")

    # Only slots the templates were actually re-run over can hold a superseded
    # row. Anything outside them — the baseball game templates that need the
    # archive — is left alone rather than judged on evidence not gathered.
    stale, kept = [], []
    for q in questions:
        slot = (q.get("sourceEventId"), q.get("type"))
        if slot not in regenerated_slots:
            continue
        if q["questionId"] in fresh_ids:
            continue
        (kept if q["questionId"] in referenced else stale).append(q)

    print(f"  superseded and unreferenced : {len(stale)}  (deletable)")
    print(f"  superseded but still in use : {len(kept)}  (kept — rebuild those quizzes first)")

    if kept:
        dates = {z["quizDate"] for z in quizzes
                 if referenced & {r["questionId"] for r in kept}
                 and set(z.get("questionIds") or []) & {r["questionId"] for r in kept}}
        print(f"    quizzes still pointing at old wording: {len(dates)}")

    if args.retire:
        # Deleting is not enough on its own. A referenced row cannot be deleted
        # until the quizzes have moved, and the quizzes will not move while the
        # row is still `approved` — the assembler keeps choosing it, so the
        # rebuild puts it straight back and the prune keeps sparing it. Marking
        # it rejected breaks that circle: the assembler stops seeing it, the
        # next rebuild moves off it, and the delete then has nothing to spare.
        with qt.batch_writer(overwrite_by_pkeys=["questionId"]) as batch:
            for r in stale + kept:
                item = dict(r)
                item["status"] = "rejected"
                item["reviewFlags"] = sorted(set(
                    (item.get("reviewFlags") or [])
                    + ["superseded by a reworded template"]))
                batch.put_item(Item=item)
        print(f"\nretired {len(stale) + len(kept)} superseded questions "
              f"(status=rejected). Rebuild the quizzes, then run --apply.")
        return

    if not args.apply:
        print("\ndry run - nothing deleted.")
        return

    with qt.batch_writer() as batch:
        for r in stale:
            batch.delete_item(Key={"questionId": r["questionId"]})
    print(f"\ndeleted {len(stale)} superseded questions")


if __name__ == "__main__":
    main()
