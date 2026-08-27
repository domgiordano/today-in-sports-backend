#!/usr/bin/env python3
"""
Rebuild the question bank from the stored events after a template change.

    python scripts/regenerate_questions.py --dry-run
    python scripts/regenerate_questions.py --write

A question's id is a hash of its own content, prompt included, which is what
makes the bank self-correcting: change what a template says and the same event
produces a *different* question. The new one is written and the old one becomes
dead inventory. That property is only useful if something actually re-runs the
templates, and nothing did — `cron_ingest_recent` only touches the last few
days, because everything older is immutable history.

So a prompt fix reached nobody. The bank still held the sentence the template
used to emit.

Events are the input, not the source archives. Winter sports and transactions
both build their distractor context from the events themselves, so the whole
corpus regenerates from DynamoDB without re-fetching Retrosheet or anyone else.
The exception is the baseball templates that draw distractors from *that day's
other games*, which the events table cannot supply — those are skipped and
reported rather than regenerated with a thinner pool than they had.

Order matters when this is applied, and it is the same order `rekey_questions`
uses for the same reason:

    1. write the new questions       (both old and new exist)
    2. approve them
    3. rebuild the published quizzes (readers move to the new ids)
    4. prune the superseded old rows (last, once nothing points at them)

Steps 3 and 4 are deliberately not in this script. It writes and reports; the
moving of live quizzes is a separate, reversible decision.
"""

import argparse
import collections
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3                                                     # noqa: E402

from lambdas.common import constants                             # noqa: E402
from lambdas.common.templates import mlb_templates as mlb_tpl    # noqa: E402
from lambdas.common.templates import transaction_templates as tx_tpl  # noqa: E402
from lambdas.common.templates import winter_templates as winter_tpl   # noqa: E402

WINTER_SPORTS = ("nhl", "nba", "soccer", "nfl", "f1")
# The reason codes the transaction detectors emit, read off the events table
# rather than guessed — the first guess matched nothing and regenerated no
# transaction questions at all, silently.
TRANSACTION_REASONS = {"star_free_agent", "star_trade", "blockbuster_trade",
                       "star_purchase", "landmark_sale", "star_drafted"}

# Baseball game templates draw distractors from that day's *other games*, which
# the events table cannot supply. Regenerating them here would hand them a
# thinner pool than they were built with, so they are left alone; a change to
# one of those needs the Retrosheet archive and `generate_questions.py`.
MLB_GAME_REASONS_SKIPPED = True


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if v != "" and v is not None}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return Decimal(str(o))
    return o


def _plain(o):
    """Decimal back to int/float so template arithmetic behaves."""
    if isinstance(o, list):
        return [_plain(v) for v in o]
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    return o


def scan(table):
    out, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        out.extend(_plain(i) for i in page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return out
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb")
    events = scan(dynamo.Table(constants.EVENTS_TABLE_NAME))
    existing = scan(dynamo.Table(constants.QUESTIONS_TABLE_NAME))
    print(f"events         : {len(events)}")
    print(f"questions today: {len(existing)}")

    winter = [e for e in events if e.get("sport") in WINTER_SPORTS]
    tx = [e for e in events
          if e.get("sport") == "mlb" and e.get("reason") in TRANSACTION_REASONS]

    fresh = []
    fresh += winter_tpl.generate(winter, winter_tpl.build_context(winter))
    if tx:
        fresh += tx_tpl.generate(tx, tx_tpl.build_context(tx))

    valid = [q for q in fresh if not mlb_tpl.validate(q)]
    print(f"regenerated    : {len(fresh)}  ({len(valid)} valid)")

    old_by_id = {q["questionId"]: q for q in existing}
    new_ids = {q["questionId"] for q in valid}
    added = [q for q in valid if q["questionId"] not in old_by_id]

    # A stored question is superseded when the same event and format now
    # produces a different id — that is the template having changed its wording.
    regenerated_slots = {(q.get("sourceEventId"), q.get("type")) for q in valid}
    superseded = [q for q in existing
                  if q["questionId"] not in new_ids
                  and (q.get("sourceEventId"), q.get("type")) in regenerated_slots]

    print(f"  new          : {len(added)}")
    print(f"  unchanged    : {len(valid) - len(added)}")
    print(f"  superseded   : {len(superseded)} (old wording, now dead inventory)")

    by_sport = collections.Counter(q["sport"] for q in added)
    print("\nnew questions by sport:", dict(by_sport.most_common()))

    if added:
        print("\nsample of the new wording:")
        for q in added[:5]:
            print(f"   [{q['sport']}/{q['type']}] {q['prompt'][:96]}")

    if not args.write:
        print("\ndry run - nothing written.")
        return

    qt = dynamo.Table(constants.QUESTIONS_TABLE_NAME)
    with qt.batch_writer(overwrite_by_pkeys=["questionId"]) as batch:
        for q in valid:
            item = dict(q)
            item["sportTier"] = f"{q['sport']}#{q['tier']}"
            # An id that already exists keeps whatever review status it earned.
            if q["questionId"] in old_by_id:
                item["status"] = old_by_id[q["questionId"]].get("status", "draft")
            batch.put_item(Item=_clean(item))
    print(f"\nwrote {len(valid)} questions ({len(added)} of them new)")
    print(f"{len(superseded)} superseded rows left in place — prune them only "
          f"after the published quizzes have been rebuilt")
    print("\nnext: python scripts/auto_review.py --apply")


if __name__ == "__main__":
    main()
