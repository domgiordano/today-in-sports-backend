#!/usr/bin/env python3
"""
Draft questions from narrative candidates by restating their cited sentence.

    python scripts/draft_narrative.py --dry-run
    python scripts/draft_narrative.py --apply

The rule this source runs under permits exactly this and no more:

    nothing may assert a fact; a question may only restate a sentence it was
    given, and that sentence is shown to the reviewer beside the question.

So every answer below appears verbatim in the candidate's own sentence. Where
the sentence hedges - "Lara was reported today to have quit" - the question
hedges with it, because the alternative is asserting something the source did
not. Where a sentence only supports a headline and no checkable fact, no
question is written from it at all; of twenty-six candidates at the top of the
queue, eight had a fact worth asking about.

These land as `draft`, not `approved`. Hand-written questions skip review
because a person has already read the source; a restatement has had no such
reading, so it goes to the queue like anything else and carries
`machineAuthored` so it is obvious which is which.

Distractors are a separate matter and worth stating plainly: a wrong answer
asserts nothing, so the only requirement on it is that it be real and
contemporaneous. An invented club or an anachronistic one is solvable by
elimination, which is the failure this whole corpus is built to avoid.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common import narrative_dynamo as nd   # noqa: E402

AUTHOR = "claude-code (restated from the cited sentence)"

# Each entry names the candidate by the sentence it must match, so a drift in
# the queue fails loudly rather than attaching a question to the wrong article.
DRAFTS = [
    {
        "match": "Swindon Town yesterday dismissed their assistant manager",
        "fields": {
            "type": "numeric",
            "prompt": "On this day in 2000, Swindon Town dismissed assistant "
                      "manager Mike Walsh in a cost-cutting drive. How many "
                      "other members of backroom staff went with him?",
            "answer": 14, "tolerance": 2,
        },
    },
    {
        "match": "Denis Irwin quit international football",
        "fields": {
            "type": "numeric",
            "prompt": "Denis Irwin retired from international football on this "
                      "day in 2000. How many caps had he won for the Republic "
                      "of Ireland?",
            "answer": 56, "tolerance": 3,
        },
    },
    {
        "match": "Charlton yesterday signed the Sweden striker Mathias Svensson",
        "fields": {
            "type": "mc",
            "prompt": "On this day in 2000, which club signed the Sweden "
                      "striker Mathias Svensson from Crystal Palace?",
            "answer": "Charlton",
            "distractors": ["Ipswich Town", "Norwich City",
                            "Wolverhampton Wanderers"],
        },
    },
    {
        "match": "Wasim Akram has resigned as captain of Pakistan",
        "fields": {
            "type": "mc",
            "prompt": "Wasim Akram resigned as Pakistan's cricket captain on "
                      "this day in 2000. Who took charge for the next one-day "
                      "international?",
            "answer": "Saeed Anwar",
            "distractors": ["Inzamam-ul-Haq", "Moin Khan", "Saqlain Mushtaq"],
        },
    },
    {
        "match": "John Barnes has been sacked as manager of Celtic",
        "fields": {
            "type": "mc",
            "prompt": "After John Barnes was sacked as Celtic manager on this "
                      "day in 2000, who took temporary charge of first-team "
                      "affairs?",
            "answer": "Kenny Dalglish",
            "distractors": ["Martin O'Neill", "Tommy Burns", "Wim Jansen"],
        },
    },
    {
        # The source hedges, so the question does too. Asserting that he quit
        # would be stating something the sentence declines to state.
        "match": "Brian Lara was reported today to have quit",
        "fields": {
            "type": "mc",
            "prompt": "On this day in 2000, which batsman was reported to have "
                      "stepped down as West Indies captain?",
            "answer": "Brian Lara",
            "distractors": ["Courtney Walsh", "Carl Hooper", "Jimmy Adams"],
        },
    },
    {
        "match": "Alan Shearer announced tonight that he plans to end his "
                 "international career",
        "fields": {
            "type": "mc",
            "prompt": "On this day in 2000, England captain Alan Shearer said "
                      "he would end his international career after which "
                      "tournament?",
            "answer": "Euro 2000",
            "distractors": ["the 1998 World Cup", "Euro 96",
                            "the 2002 World Cup"],
        },
    },
    {
        "match": "Frank Leboeuf was banned for two matches",
        "fields": {
            "type": "numeric",
            "prompt": "On this day in 2000, Chelsea's Frank Leboeuf was banned "
                      "for stamping on Harry Kewell. How many matches did the "
                      "ban run to?",
            "answer": 2, "tolerance": 1,
        },
    },
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    candidates = nd.list_candidates(limit=5000)
    by_sentence = {nd.cited_sentence(c): c for c in candidates}

    written, skipped = 0, 0
    for draft in DRAFTS:
        found = next((c for sentence, c in by_sentence.items()
                      if draft["match"] in sentence), None)
        if not found:
            print(f"  no candidate matches: {draft['match'][:60]}")
            skipped += 1
            continue

        problems = nd.validate(draft["fields"], found)
        if problems:
            print(f"  invalid: {draft['match'][:44]} -> {'; '.join(problems)}")
            skipped += 1
            continue

        answer = draft["fields"]["answer"]
        print(f"  {found['gameDate']}  {draft['fields']['type']:7s} "
              f"-> {answer}")
        print(f"     {draft['fields']['prompt'][:96]}")

        if args.apply:
            nd.question_from_candidate(found, draft["fields"], AUTHOR,
                                       machine_authored=True)
            nd.set_candidate_status(found["mmdd"], found["yearEventId"],
                                    "written", AUTHOR)
        written += 1

    print(f"\n{'wrote' if args.apply else 'would write'}: {written}"
          f"   skipped: {skipped}")
    if not args.apply:
        print("dry run - nothing written")


if __name__ == "__main__":
    main()
