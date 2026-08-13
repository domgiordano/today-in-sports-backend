#!/usr/bin/env python3
"""
Load detected events and generated questions into DynamoDB.

    python scripts/load_corpus.py --events all_events.json
    python scripts/load_corpus.py --events all_events.json --dry-run

Idempotent: events key on (mmdd, year#gameId) and questions on questionId, so
re-running overwrites rather than duplicating. That matters because the corpus
is built incrementally — NBA finishes hours after MLB — and this will be run
several times as sources land.

New questions are written as `draft`. Nothing here approves anything; that is
the review queue's job. An existing decision - approved, rejected or used - is
read back and preserved, because a reload regenerates the question but has no
business overturning the verdict on it.
"""

import argparse
import collections
import json
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common import constants                              # noqa: E402
from lambdas.common.templates import lineup_templates as lineup_tpl  # noqa: E402
from lambdas.common.templates import map_templates as map_tpl     # noqa: E402
from lambdas.common.templates import mlb_templates as mlb_tpl     # noqa: E402
from lambdas.common.templates import ordering_templates as ord_tpl  # noqa: E402
from lambdas.common.templates import transaction_templates as tran_tpl  # noqa: E402
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


def load_events(path):
    """
    Read the corpus, whether it is JSONL or a JSON array.

    `build_corpus.py` streams one event per line - the whole reason it can hold
    155 seasons without running out of disk. Older dumps are a single array, and
    both still turn up on disk, so both are accepted rather than making the
    caller remember which is which.
    """
    with open(path) as f:
        first = f.readline()
        f.seek(0)
        if first.lstrip().startswith("["):
            return json.load(f)
        return [json.loads(line) for line in f if line.strip()]


def status_for(question_id, decided):
    """
    The status a question should be written with.

    A reload regenerates every question from the corpus, but a decision about
    one belongs to whoever made it. This wrote "draft" unconditionally, which
    silently reverted every approval on the next reload - 17,546 of them in a
    single run - while the pruning code below carefully explained that a
    decision is not a reload's to discard. Both halves now agree.
    """
    return decided.get(question_id, "draft")


def build_questions(events, circuits=None):
    """
    Route each event to the templates for its own sport.

    Reason codes are not unique across sports — NHL and NFL both emit
    `playoff_overtime` — so the template sets are kept apart and each gets only
    its own events.
    """
    # Transactions are MLB but are not games, so they are split out first —
    # the game-level templates expect a box score and would find none.
    tran_events = [e for e in events
                   if e["reason"] in tran_tpl.TEMPLATES]
    tran_ids = {e["gameId"] for e in tran_events}

    mlb_events = [e for e in events
                  if e["sport"] == "mlb" and e["gameId"] not in tran_ids]
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

    # Transaction context is corpus-wide: distractor teams are drawn from the
    # clubs active in the same decade, so it is built once over every deal
    # rather than per date.
    questions.extend(tran_tpl.generate(tran_events))

    # Ordering and clue ladders are built over every event regardless of sport:
    # an ordering question is a comparison between events sharing a calendar
    # day, which is the one thing every source has in common.
    questions.extend(ord_tpl.generate(events))

    # Map questions need circuit coordinates, which only the f1db dump has. No
    # circuits loaded means no map questions, rather than questions pointing at
    # a place we guessed.
    if circuits:
        questions.extend(map_tpl.generate(events, map_tpl.build_context(circuits)))

    # Lineup questions need the starting nines the events now carry, and a
    # decoy pool built from the same corpus so no decoy ever really played.
    questions.extend(lineup_tpl.generate(events))
    return questions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--f1-cache", help="extracted f1db dump, for map questions")
    args = ap.parse_args()

    events = load_events(args.events)
    print(f"events loaded: {len(events)}")
    print("  by sport:", dict(collections.Counter(e["sport"] for e in events)))

    circuits = {}
    if args.f1_cache:
        from lambdas.common.sources import f1db
        circuits = f1db.load_circuits(args.f1_cache)
        print(f"circuits with coordinates: {len(circuits)}")

    questions = build_questions(events, circuits)
    valid, rejected = [], 0
    reasons = collections.Counter()
    for q in questions:
        # Validation is routed by format. The MLB validator knows about
        # distractors and numeric answers; it has no opinion on whether an
        # ordering question's items are a permutation of its answer, and
        # running everything through it would wave those questions straight
        # past the checks written for them.
        checker = (lineup_tpl.validate if q["type"] == "multi"
                   else map_tpl.validate if q["type"] == "map"
                   else ord_tpl.validate if q["type"] in ("ordering", "clue")
                   else mlb_tpl.validate)
        problems = checker(q)
        if problems:
            rejected += 1
            for p in problems:
                reasons[p] += 1
        else:
            valid.append(q)

    if args.limit:
        valid = valid[:args.limit]

    print(f"questions: {len(valid)} valid, {rejected} rejected by validation")
    for reason, count in reasons.most_common(6):
        print(f"    rejected: {count:5d}  {reason}")
    print("  by type:", dict(collections.Counter(q["type"] for q in valid)))
    print("  by tier:", dict(sorted(collections.Counter(q["tier"] for q in valid).items())))
    dates = len({q["mmdd"] for q in valid})
    print(f"  calendar dates: {dates}/366")

    if args.dry_run:
        print("\ndry run — nothing written")
        return

    import boto3
    from boto3.dynamodb.conditions import Key
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

    # Existing decisions, so a reload does not overwrite them.
    #
    # This wrote `status: "draft"` unconditionally, which silently reverted
    # every approval on the next reload - 17,546 of them, in one run - while
    # the pruning code twenty lines below carefully explains that a decision is
    # a human's and a reload has no business discarding it. Both halves now
    # agree.
    decided = {}
    for status in ("approved", "rejected", "used"):
        last_key = None
        while True:
            kwargs = {
                "IndexName": constants.QUESTIONS_STATUS_INDEX,
                "KeyConditionExpression": Key("status").eq(status),
                "ProjectionExpression": "questionId",
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            resp = questions_table.query(**kwargs)
            for item in resp.get("Items", []):
                decided[item["questionId"]] = status
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
    print(f"existing decisions preserved: {len(decided)}")

    written = 0
    with questions_table.batch_writer(overwrite_by_pkeys=["questionId"]) as batch:
        for q in valid:
            batch.put_item(Item=clean({
                **q,
                "sportTier": f"{q['sport']}#{q['tier']}",
                "status": status_for(q["questionId"], decided),
            }))
            written += 1
            if written % 2000 == 0:
                print(f"  questions written: {written}", flush=True)
    print(f"questions written: {written}")

    # Prune stale drafts.
    #
    # Writing alone is not enough: when a fix stops a question being generated,
    # the old row survives. That is how two "the CL4 routed the Pittsburgh
    # Alleghenys" questions outlived the team-code guard that was written to
    # kill them.
    #
    # Only `draft` rows are touched. Anything approved, rejected or used carries
    # a human decision, and a reload has no business discarding that.
    # Pruning is scoped to the sports this run actually rebuilt. The corpus file
    # is often one sport - build_corpus.py emits MLB, and the winter sports come
    # from their own ingest scripts - so an unscoped prune would treat every
    # other sport's drafts as stale and delete inventory this run never had an
    # opinion about.
    fresh_ids = {q["questionId"] for q in valid}
    rebuilt_sports = {q["sport"] for q in valid}
    print(f"pruning scoped to: {', '.join(sorted(rebuilt_sports))}")

    stale, last_key = [], None
    while True:
        kwargs = {
            "IndexName": constants.QUESTIONS_STATUS_INDEX,
            "KeyConditionExpression": Key("status").eq("draft"),
            "ProjectionExpression": "questionId, sport",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = questions_table.query(**kwargs)
        stale.extend(i["questionId"] for i in resp.get("Items", [])
                     if i["questionId"] not in fresh_ids
                     and i.get("sport") in rebuilt_sports)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    if stale:
        with questions_table.batch_writer() as batch:
            for qid in stale:
                batch.delete_item(Key={"questionId": qid})
    print(f"stale drafts pruned: {len(stale)}")


if __name__ == "__main__":
    main()
