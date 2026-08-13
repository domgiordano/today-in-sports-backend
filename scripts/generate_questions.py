#!/usr/bin/env python3
"""
Generate questions from detected events.

Deterministic: every prompt is assembled from fields that came out of a real
dataset row, and every answer is a value from that row. No model is consulted,
offline or otherwise.

    python scripts/generate_questions.py --games games.json --out questions.json
    python scripts/generate_questions.py --games games.json --write

Distractors are drawn from the same date's real games, so they are plausible and
contemporaneous. Invented distractors are solvable by elimination.
"""

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.notability import mlb as nb            # noqa: E402
from lambdas.common.templates import mlb_templates as tpl   # noqa: E402

QUESTIONS_TABLE = os.environ.get("QUESTIONS_TABLE_NAME", "today-in-sports-questions")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE_NAME", "today-in-sports-events")


def build(games):
    """games -> events -> questions, with per-date distractor context."""
    by_date = collections.defaultdict(list)
    for g in games:
        by_date[g["gameDate"]].append(g)

    all_events, all_questions = [], []
    for date, day_games in sorted(by_date.items()):
        events = nb.run(day_games, enrich=False)
        if not events:
            continue
        all_events.extend(events)
        all_questions.extend(tpl.generate(events, day_games))
    return all_events, all_questions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True, help="JSON from ingest_games.py --dry-run --out")
    ap.add_argument("--out", help="write questions to this file")
    ap.add_argument("--write", action="store_true", help="write to DynamoDB")
    ap.add_argument("--limit", type=int, help="cap questions emitted")
    args = ap.parse_args()

    games = json.load(open(args.games))
    print(f"games: {len(games)}")

    events, questions = build(games)
    print(f"events: {len(events)}")
    print(f"questions: {len(questions)}")

    # Validation is a gate, not a report. A question missing provenance, or with
    # a null interpolated into its prompt, is a factual defect and never reaches
    # the review queue.
    valid, rejected = [], []
    for q in questions:
        problems = tpl.validate(q)
        (rejected if problems else valid).append((q, problems))
    valid = [q for q, _ in valid]

    print(f"  valid    : {len(valid)}")
    print(f"  rejected : {len(rejected)}")
    for q, problems in rejected[:5]:
        print(f"     {problems} :: {q['prompt'][:70]}")

    if args.limit:
        valid = valid[:args.limit]

    print("\nby type :", dict(collections.Counter(q["type"] for q in valid)))
    print("by tier :", dict(sorted(collections.Counter(q["tier"] for q in valid).items())))
    dates = len({q["mmdd"] for q in valid})
    print(f"calendar dates covered: {dates} / 366")

    if args.out:
        payload = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "counts": {"events": len(events), "questions": len(valid)},
            "questions": valid,
        }
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=1, default=str)
        print(f"\nwrote {args.out}")

    if args.write:
        import boto3
        from decimal import Decimal

        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items() if v != "" and v is not None}
            if isinstance(o, list):
                return [clean(v) for v in o]
            if isinstance(o, float):
                return Decimal(str(o))
            return o

        dynamo = boto3.resource("dynamodb")
        qt = dynamo.Table(QUESTIONS_TABLE)
        with qt.batch_writer(overwrite_by_pkeys=["questionId"]) as batch:
            for q in valid:
                item = dict(q)
                item["sportTier"] = f"{q['sport']}#{q['tier']}"
                batch.put_item(Item=clean(item))
        print(f"\nwrote {len(valid)} questions to {QUESTIONS_TABLE}")


if __name__ == "__main__":
    main()
