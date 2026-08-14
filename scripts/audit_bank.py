#!/usr/bin/env python3
"""
Audit the live question bank against the corpus it came from.

    python scripts/audit_bank.py --events all_events.jsonl
    python scripts/audit_bank.py --events all_events.jsonl --show 5

Validation asks "is this well-formed" and runs at generation time. Auto-review
asks "would a person think this was a good question" and runs over drafts.
Neither asks the question this does: **is what is actually stored in DynamoDB
still true, and does it still match its source?**

That gap is not theoretical. Pruning once touched drafts only, on the reasoning
that an approval belongs to whoever made it - which quietly assumed the approved
question still existed. It did not: 5,593 clue ladders reading "This happened in
the 2000s. The sport was baseball. He signed as a free agent." sat approved and
quiz-eligible for weeks after the rewrite that made them ungeneratable.

Every check here compares stored rows against a freshly generated corpus or
against the row's own internal consistency. Nothing is asked of a model, for the
same reason nothing else in this project is: a plausible wrong judgement about
whether a question is right is undetectable at audit time.
"""

import argparse
import collections
import importlib.util
import json
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3                                              # noqa: E402
from boto3.dynamodb.conditions import Key                 # noqa: E402

from lambdas.common import constants                      # noqa: E402


def _load_loader():
    path = os.path.join(os.path.dirname(__file__), "load_corpus.py")
    spec = importlib.util.spec_from_file_location("load_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scan(table, statuses=("approved", "draft", "used")):
    """Every question in the given statuses."""
    out = []
    for status in statuses:
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
                item["_status"] = status
                out.append(item)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
    return out


# --------------------------------------------------------------------- checks

def check_date_anchoring(q):
    """
    The one thing a date-anchored quiz cannot survive.

    `mmdd` decides which day a question is asked on. If it disagrees with the
    year or with the event it came from, the question is asked on a day it did
    not happen - and nothing downstream can tell.
    """
    problems = []
    mmdd, year = q.get("mmdd"), q.get("year")

    if not (isinstance(mmdd, str) and re.fullmatch(r"\d{2}-\d{2}", mmdd)):
        problems.append("mmdd is not MM-DD")
        return problems

    month, day = int(mmdd[:2]), int(mmdd[3:])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        problems.append("mmdd is not a real date")
    if month == 2 and day == 29:
        # Legal, but only askable in a leap year - worth knowing the count.
        problems.append("february 29")

    if not year or not (1850 <= int(year) <= 2030):
        problems.append("year outside the corpus range")

    return problems


def check_prompt_integrity(q):
    """Things that make a prompt unusable however well-formed it is."""
    problems = []
    prompt = q.get("prompt") or ""

    if "None" in prompt or "null" in prompt:
        problems.append("null interpolated into the prompt")
    # An unresolved Retrosheet club code reads as a typo to a player and as a
    # missing lookup to anyone debugging it.
    if re.search(r"\bthe [A-Z]{2}\d\b", prompt):
        problems.append("unresolved team code in the prompt")
    if re.search(r"\s{2,}", prompt):
        problems.append("double spacing, usually a dropped field")
    if prompt != prompt.strip():
        problems.append("leading or trailing whitespace")

    year = q.get("year")
    if year and str(year) in prompt and q.get("type") == "ordering":
        # An ordering question's answer is the chronology; printing a year in
        # the prompt gives part of it away.
        problems.append("ordering prompt contains a year")

    return problems


def check_provenance(q):
    """A question nobody can check is a question nobody should ship."""
    problems = []
    ref = q.get("sourceDatasetRef") or ""
    if not ref:
        problems.append("no source reference")
    if not q.get("sourceName"):
        problems.append("no source name")
    if q.get("sport") == "news" and not q.get("citedSentence"):
        problems.append("narrative question with no cited sentence")
    return problems


def check_answer_shape(q):
    """The answer has to be the shape its format grades against."""
    problems = []
    qtype, answer = q.get("type"), q.get("answer")

    if qtype == "map":
        if not (isinstance(answer, dict) and "lat" in answer and "lng" in answer):
            problems.append("map answer is not a coordinate")
        else:
            lat, lng = float(answer["lat"]), float(answer["lng"])
            if lat == 0 and lng == 0:
                problems.append("coordinate is null island")
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                problems.append("coordinate is off the globe")
    elif qtype == "ordering":
        if not isinstance(answer, list) or len(answer) != 4:
            problems.append("ordering answer is not four items")
        elif sorted(map(str, answer)) != sorted(map(str, q.get("items") or [])):
            problems.append("items are not a permutation of the answer")
    elif qtype == "multi":
        if not isinstance(answer, list) or not answer:
            problems.append("multi answer is not a list")
        else:
            options = set(map(str, q.get("options") or []))
            if options and not set(map(str, answer)) <= options:
                problems.append("a correct pick is missing from the options")
    elif qtype == "numeric":
        if q.get("numericAnswer") is None:
            problems.append("no numeric answer")
    else:
        if not (isinstance(answer, str) and answer.strip()):
            problems.append("empty answer")

    return problems


def check_against_corpus(q, fresh_by_id):
    """
    Does the stored row still match what the generator produces today?

    A question whose id is absent has been superseded - the generator changed
    and this row is a leftover. A question whose id is present but whose content
    differs means the loader and the bank have drifted, which should be
    impossible and is worth knowing immediately if it happens.
    """
    problems = []
    if q.get("authoredBy"):
        return problems  # hand-written; no generator produces it
    if q.get("sport") == "news":
        return problems

    fresh = fresh_by_id.get(q["questionId"])
    if fresh is None:
        problems.append("superseded - the generator no longer produces this")
        return problems

    for field in ("prompt", "answer", "type", "tier", "mmdd", "year",
                  "sourceDatasetRef"):
        if _normalise(q.get(field)) != _normalise(fresh.get(field)):
            problems.append(f"{field} differs from the generated question")
    return problems


def _normalise(value):
    """
    Compare stored against generated without tripping over storage types.

    DynamoDB hands numbers back as `Decimal`, so a map answer round-trips as
    `{'lat': Decimal('40.66')}` against a generated `{'lat': 40.66}`. Comparing
    those as strings reported all 2,059 map questions as corrupted on the first
    run of this script - a false positive severe enough to have buried a real
    one, which is exactly what an audit must not do.
    """
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return value


CHECKS = (
    ("date anchoring", check_date_anchoring),
    ("prompt integrity", check_prompt_integrity),
    ("provenance", check_provenance),
    ("answer shape", check_answer_shape),
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--f1-cache")
    ap.add_argument("--accolades")
    ap.add_argument("--parks")
    ap.add_argument("--show", type=int, default=3,
                    help="example questions to print per problem")
    args = ap.parse_args()

    loader = _load_loader()

    events = loader.load_events(args.events)
    circuits = {}
    if args.f1_cache:
        from lambdas.common.sources import f1db
        circuits = f1db.load_circuits(args.f1_cache)
    accolades = json.load(open(args.accolades)) if args.accolades else {}
    parks = json.load(open(args.parks)) if args.parks else {}

    fresh = loader.build_questions(events, circuits, accolades, parks)
    fresh_by_id = {q["questionId"]: q for q in fresh}
    print(f"corpus events            : {len(events)}")
    print(f"questions the generator produces: {len(fresh)}")

    # Colliding ids are silent data loss: the later write wins and the earlier
    # question simply never reaches the bank. Worse, which one survives depends
    # on generation order, so an approved id can come to mean something else.
    # 61 questions were being lost this way before the answer became part of
    # the id, so this is checked over the whole corpus and not only in a test.
    collisions = len(fresh) - len(fresh_by_id)
    if collisions:
        duplicated = collections.Counter(q["questionId"] for q in fresh)
        worst = [qid for qid, n in duplicated.most_common(3) if n > 1]
        print(f"  !! {collisions} questions share an id with another and will "
              f"overwrite it on write")
        for qid in worst:
            for q in fresh:
                if q["questionId"] == qid:
                    print(f"       {qid} {q['type']} {q['mmdd']} "
                          f"-> {str(q.get('answer'))[:40]}")
    else:
        print("question ids             : all distinct")

    table = boto3.resource("dynamodb").Table(constants.QUESTIONS_TABLE_NAME)
    stored = scan(table)
    print(f"questions stored         : {len(stored)}")
    print()

    problems = collections.Counter()
    examples = collections.defaultdict(list)
    affected = set()

    for q in stored:
        found = []
        for _, check in CHECKS:
            found.extend(check(q))
        found.extend(check_against_corpus(q, fresh_by_id))
        for p in found:
            problems[p] += 1
            if len(examples[p]) < args.show:
                examples[p].append(q)
        if found:
            affected.add(q["questionId"])

    if not problems:
        print("no problems found")
        return

    print(f"questions with a problem : {len(affected)} "
          f"({100 * len(affected) / max(len(stored), 1):.1f}%)")
    print()
    for p, n in problems.most_common():
        print(f"  {n:6d}  {p}")
    print()

    for p, _ in problems.most_common():
        print(f"--- {p}")
        for q in examples[p]:
            print(f"    [{q['_status']}] {q.get('type')} {q.get('mmdd')} "
                  f"{q.get('year')} {q.get('sport')}")
            print(f"    {(q.get('prompt') or '')[:120]}")
        print()


if __name__ == "__main__":
    main()
