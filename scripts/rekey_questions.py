#!/usr/bin/env python3
"""
Restore the invariant that a question's id hashes its own content.

    python scripts/rekey_questions.py --dry-run
    python scripts/rekey_questions.py --apply

A question id is sha1 of (event, type, prompt, answer). That is what makes the
bank self-correcting: change what a question says and it becomes a different
question, so the old one is pruned and the new one is written. Nothing has to
remember that an edit happened.

Editing a stored prompt in place breaks it. The row keeps an id that no longer
describes it, and the next generation run computes the *correct* id for the same
content, finds no such row, and writes a second one - two approved questions,
identical text, different ids, both eligible for the same date. That is the
failure this repairs, for the 557 lineup questions whose prompts were corrected
in the bank rather than regenerated.

Re-keying is safe precisely because the content does not change. A player sees
the same question either way; only the key moves. The order below is the whole
of the care needed:

    1. write the new rows          (both ids exist - nothing points at nothing)
    2. repoint every quiz          (readers follow to the new id)
    3. delete the old rows         (last, once nothing references them)

Run 1 and 2 without 3 and the bank is merely redundant. Run 3 first and 38
quizzes resolve to nothing, 27 of them published.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import boto3                                                     # noqa: E402

from lambdas.common import constants                             # noqa: E402
from lambdas.common.templates import lineup_templates as lineup  # noqa: E402


def _scan(table, **kwargs):
    out, last_key = [], None
    while True:
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.scan(**kwargs)
        out.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return out


def correct_id(question):
    """
    The id this question's own content implies.

    Only the templates that hash a prompt are covered, because only they can
    drift this way - and `_qid` is the one function that must agree with the
    template that wrote the row, so it is called rather than reimplemented.
    """
    return lineup._qid(question.get("sourceEventId"), question["type"],
                       question["prompt"], question.get("answer"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--type", default="multi",
                    help="question type to check (default: multi)")
    args = ap.parse_args()

    dynamo = boto3.resource("dynamodb")
    questions_table = dynamo.Table(constants.QUESTIONS_TABLE_NAME)
    quizzes_table = dynamo.Table(constants.QUIZZES_TABLE_NAME)

    questions = _scan(questions_table)
    existing = {q["questionId"] for q in questions}

    remap, rows = {}, {}
    for q in questions:
        if q.get("type") != args.type:
            continue
        wanted = correct_id(q)
        if wanted != q["questionId"]:
            remap[q["questionId"]] = wanted
            rows[q["questionId"]] = q

    print(f"questions whose id does not match their content: {len(remap)}")
    if not remap:
        return

    # Refuse rather than merge. A collision means two rows genuinely claim the
    # same content, and picking one is a judgement this cannot make.
    collisions = [new for new in remap.values() if new in existing]
    if collisions:
        raise SystemExit(f"{len(collisions)} new ids already exist - stopping")
    if len(set(remap.values())) != len(remap):
        raise SystemExit("two questions want the same new id - stopping")

    quizzes = _scan(quizzes_table)
    affected = [z for z in quizzes
                if any(i in remap for i in (z.get("questionIds") or []))]
    print(f"quizzes to repoint: {len(affected)} "
          f"({sum(1 for z in affected if z.get('status') == 'published')} published)")

    if not args.apply:
        for old, new in list(remap.items())[:3]:
            print(f"  {old} -> {new}")
            print(f"      {rows[old]['prompt'][:88]}")
        print("\ndry run - nothing written")
        return

    # 1. New rows first, so every id a reader might follow exists.
    with questions_table.batch_writer() as batch:
        for old, new in remap.items():
            batch.put_item(Item=dict(rows[old], questionId=new,
                                     rekeyedFrom=old))
    print(f"wrote {len(remap)} rows under their correct ids")

    # 2. Repoint readers.
    for quiz in affected:
        ids = [remap.get(i, i) for i in quiz["questionIds"]]
        quizzes_table.update_item(
            Key={"quizDate": quiz["quizDate"]},
            UpdateExpression="SET questionIds = :q",
            ExpressionAttributeValues={":q": ids},
        )
    print(f"repointed {len(affected)} quizzes")

    # 3. Only now are the old rows unreferenced.
    with questions_table.batch_writer() as batch:
        for old in remap:
            batch.delete_item(Key={"questionId": old})
    print(f"deleted {len(remap)} superseded rows")


if __name__ == "__main__":
    main()
