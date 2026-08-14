"""
Rewrite lineup prompts that never named the game.

"four of these eight players started this game" was asked against a date and
eight names, and never said which game — there is no way to reason toward that
answer, only to recognise the names. The template names the fixture now; these
are the questions written before it did.

The teams live on the event, not the question, so this joins the two by
sourceEventId. Events are scanned once into memory rather than fetched per
question: there are a few thousand of them and 564 point lookups against a
table keyed on something else would be a scan apiece.

    python3 scripts/fix_lineup_prompts.py            # dry run, prints changes
    python3 scripts/fix_lineup_prompts.py --apply    # writes them
"""

import sys

import boto3

from lambdas.common import constants
from lambdas.common.templates.lineup_templates import teams_in
from lambdas.common.templates.mlb_templates import pretty_date

OLD = "started this game"
APPLY = "--apply" in sys.argv


def scan(table, **kwargs):
    last = None
    while True:
        if last:
            kwargs["ExclusiveStartKey"] = last
        resp = table.scan(**kwargs)
        yield from resp.get("Items", [])
        last = resp.get("LastEvaluatedKey")
        if not last:
            return


def main():
    dynamo = boto3.resource("dynamodb")
    questions = dynamo.Table(constants.QUESTIONS_TABLE_NAME)
    events = dynamo.Table(constants.EVENTS_TABLE_NAME)

    by_game = {}
    for event in scan(events):
        game_id = event.get("gameId")
        if not game_id:
            continue
        one, other = teams_in(event.get("facts") or {})
        if one and other:
            by_game[str(game_id)] = (one, other, event.get("gameDate"))
    print(f"events with named teams: {len(by_game)}")

    fixed = skipped = 0
    for q in scan(questions):
        prompt = str(q.get("prompt") or "")
        if OLD not in prompt:
            continue

        found = by_game.get(str(q.get("sourceEventId")))
        if not found:
            # The game cannot be named, so the question cannot be answered by
            # reasoning. Retired rather than guessed at — inventing a matchup
            # to fill the sentence is the one thing this corpus must not do.
            skipped += 1
            if APPLY:
                questions.update_item(
                    Key={"questionId": q["questionId"]},
                    UpdateExpression="SET #s = :s, rejectionReason = :r",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={
                        ":s": "rejected",
                        ":r": "names no game: the source event records no teams",
                    },
                )
            continue

        one, other, game_date = found
        new = (f"{pretty_date(game_date)}: the {one} played the {other}. "
               f"Four of these eight players started that game. Which four?")

        if fixed < 3:
            print(f"\n  old: {prompt}\n  new: {new}")

        if APPLY:
            questions.update_item(
                Key={"questionId": q["questionId"]},
                UpdateExpression="SET prompt = :p",
                ExpressionAttributeValues={":p": new},
            )
        fixed += 1

    verb = "rewrote" if APPLY else "would rewrite"
    retired = "retired" if APPLY else "would retire"
    print(f"\n{verb} {fixed}; {retired} {skipped} whose event names no teams")


main()
