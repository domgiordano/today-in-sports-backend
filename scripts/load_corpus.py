#!/usr/bin/env python3
"""
Load detected events and generated questions into DynamoDB.

    python scripts/load_corpus.py --events all_events.json
    python scripts/load_corpus.py --events all_events.json --dry-run

Idempotent: events key on (mmdd, year#gameId) and questions on questionId, so
re-running overwrites rather than duplicating. That matters because the corpus
is built incrementally — NBA finishes hours after MLB — and this will be run
several times as sources land.

Questions are written as `draft`. Nothing here approves anything; that is the
review queue's job and it is deliberately a human one.
"""

import argparse
import collections
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common import constants                              # noqa: E402
from lambdas.common.templates import mlb_templates as mlb_tpl     # noqa: E402
from lambdas.common.templates import winter_templates as win_tpl  # noqa: E402


def clean(obj):
    """DynamoDB rejects empty strings and floats."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if v != "" and v is not None}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def build_questions(events):
    """
    Route each event to the templates for its own sport.

    Reason codes are not unique across sports — NHL and NFL both emit
    `playoff_overtime` — so the template sets are kept apart and each gets only
    its own events.
    """
    mlb_events = [e for e in events if e["sport"] == "mlb"]
    winter_events = [e for e in events
                     if e["sport"] in ("nhl", "nfl", "f1", "nba", "soccer")]

    questions = []

    # MLB game-level templates need same-day context for real distractors.
    by_date = collections.defaultdict(list)
    for e in mlb_events:
        by_date[e["gameDate"]].append(e)
    for day_events in by_date.values():
        questions.extend(mlb_tpl.generate(day_events, []))

    # Milestones carry their own context, drawn from the milestone events.
    milestone_events = [e for e in mlb_events
                        if e["reason"] in ("pitcher_win_milestone",
                                           "player_debut", "player_finale")]
    questions.extend(mlb_tpl.generate_milestones(milestone_events))

    questions.extend(win_tpl.generate(winter_events))
    return questions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    events = json.load(open(args.events))
    print(f"events loaded: {len(events)}")
    print("  by sport:", dict(collections.Counter(e["sport"] for e in events)))

    questions = build_questions(events)
    valid, rejected = [], 0
    for q in questions:
        if mlb_tpl.validate(q):
            rejected += 1
        else:
            valid.append(q)

    if args.limit:
        valid = valid[:args.limit]

    print(f"questions: {len(valid)} valid, {rejected} rejected by validation")
    print("  by type:", dict(collections.Counter(q["type"] for q in valid)))
    print("  by tier:", dict(sorted(collections.Counter(q["tier"] for q in valid).items())))
    dates = len({q["mmdd"] for q in valid})
    print(f"  calendar dates: {dates}/366")

    if args.dry_run:
        print("\ndry run — nothing written")
        return

    import boto3
    dynamo = boto3.resource("dynamodb")

    events_table = dynamo.Table(constants.EVENTS_TABLE_NAME)
    written = 0
    with events_table.batch_writer(
            overwrite_by_pkeys=["mmdd", "yearEventId"]) as batch:
        for e in events:
            batch.put_item(Item=clean({
                **e,
                "yearEventId": f"{e['year']}#{e['gameId']}",
            }))
            written += 1
            if written % 2000 == 0:
                print(f"  events written: {written}", flush=True)
    print(f"events written: {written}")

    questions_table = dynamo.Table(constants.QUESTIONS_TABLE_NAME)
    written = 0
    with questions_table.batch_writer(overwrite_by_pkeys=["questionId"]) as batch:
        for q in valid:
            batch.put_item(Item=clean({
                **q,
                "sportTier": f"{q['sport']}#{q['tier']}",
                "status": "draft",
            }))
            written += 1
            if written % 2000 == 0:
                print(f"  questions written: {written}", flush=True)
    print(f"questions written: {written}")


if __name__ == "__main__":
    main()
