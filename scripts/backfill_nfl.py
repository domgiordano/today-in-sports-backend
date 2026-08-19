#!/usr/bin/env python3
"""
Backfill NFL history from nflverse.

    python scripts/backfill_nfl.py --dry-run
    python scripts/backfill_nfl.py --write

The NFL held 126 events against baseball's 9,543, which read as a data problem
and was not one: four of the five detectors required a playoff game, so 6,967
regular-season games across 27 seasons could only ever surface via a 90-point
shootout. Regular-season detectors fixed the looking; this fixes the looked-at,
by running the whole nflverse archive through them once.

`cron_ingest_recent` deliberately only refreshes the last N days — everything
older is immutable history that is backfilled once and never re-fetched — so
there is no scheduled job that would pick these up on its own.

Questions land as `draft`, like every other generator here. Run
`scripts/auto_review.py --apply` afterwards to promote the clean ones.
"""

import argparse
import collections
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.notability import nfl as nfl_nb              # noqa: E402
from lambdas.common.sources import nba_franchises                # noqa: E402
from lambdas.common.sources import nflverse as nfl_src            # noqa: E402
from lambdas.common.templates import mlb_templates as mlb_tpl    # noqa: E402
from lambdas.common.templates import winter_templates as tpl     # noqa: E402

CACHE_DIR = os.environ.get("NFL_CACHE_DIR", "/tmp/nflverse")
QUESTIONS_TABLE = os.environ.get("QUESTIONS_TABLE_NAME", "today-in-sports-questions")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE_NAME", "today-in-sports-events")


def build(games):
    """
    games -> events -> questions.

    Context is built once across the whole corpus rather than per date, which
    is what `build_context` asks for: four genuine NFL franchises is a real
    question, whereas the handful of teams that happened to play the same
    Thursday night is a giveaway when only one game was on.
    """
    events = nfl_nb.run(games)
    ctx = tpl.build_context(events)
    questions = tpl.generate(events, ctx)
    return events, questions


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if v != "" and v is not None}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    if isinstance(o, float):
        return Decimal(str(o))
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write to DynamoDB")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # The source adapter, not a second parser. It resolves team abbreviations
    # to full names via the teams asset — a hand-rolled reader here produced
    # prompts reading "the CLE were held scoreless", which the validator
    # correctly refused as an unresolved team code.
    os.makedirs(CACHE_DIR, exist_ok=True)
    games = nfl_src.load_games(CACHE_DIR)
    print(f"games      : {len(games)}")

    events, questions = build(games)
    print(f"events     : {len(events)}")
    print(f"questions  : {len(questions)}")

    # Validation is a gate, not a report — the same rule the baseball generator
    # applies. A question with a null interpolated into its prompt is a factual
    # defect and never reaches the review queue.
    # mlb_templates owns the shared validator; the cron validates winter
    # questions with it too.
    valid, rejected = [], []
    for q in questions:
        problems = mlb_tpl.validate(q)
        (rejected if problems else valid).append((q, problems))
    valid = [q for q, _ in valid]

    print(f"  valid    : {len(valid)}")
    print(f"  rejected : {len(rejected)}")
    for q, problems in rejected[:5]:
        print(f"     {problems} :: {q['prompt'][:70]}")

    print("\nby reason :", dict(collections.Counter(e["reason"] for e in events).most_common()))
    print("by type   :", dict(collections.Counter(q["type"] for q in valid)))
    print("by tier   :", dict(sorted(collections.Counter(q["tier"] for q in valid).items())))
    print(f"calendar dates covered: {len({q['mmdd'] for q in valid})} / 366")

    if not args.write:
        print("\ndry run — nothing written. Pass --write to persist.")
        return

    import boto3
    dynamo = boto3.resource("dynamodb")

    et = dynamo.Table(EVENTS_TABLE)
    with et.batch_writer(overwrite_by_pkeys=["gameId"]) as batch:
        for e in events:
            # Same composite the cron writes; the events table is queried by it.
            batch.put_item(Item=_clean({
                **e, "yearEventId": f"{e['year']}#{e['gameId']}"}))
    print(f"\nwrote {len(events)} events to {EVENTS_TABLE}")

    qt = dynamo.Table(QUESTIONS_TABLE)
    with qt.batch_writer(overwrite_by_pkeys=["questionId"]) as batch:
        for q in valid:
            item = dict(q)
            item["sportTier"] = f"{q['sport']}#{q['tier']}"
            batch.put_item(Item=_clean(item))
    print(f"wrote {len(valid)} questions to {QUESTIONS_TABLE} as draft")
    print("\nnext: python scripts/auto_review.py --apply")


if __name__ == "__main__":
    main()
