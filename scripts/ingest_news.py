#!/usr/bin/env python3
"""
Harvest narrative sports events from the Guardian archive.

    python scripts/ingest_news.py --from 2008-12-01 --to 2008-12-14 --out news.jsonl

This is the source that required changing the founding rule. Everything else in
the corpus derives notability from structured data and no model ever supplies a
fact. Narrative events cannot work that way - no dataset has a field meaning
"was named the starter" - so the rule became:

    a model may never assert a fact; it may only restate a sentence it was
    given, and that sentence is shown to the reviewer beside the question.

This script does the first half: it collects cited sentences with resolvable
dates. It writes candidates as `needs_review` and never as questions, because
turning a sentence into a question is the step that needs a human looking at
the sentence next to it.

Roughly two in five articles survive: the rest either read as routine coverage
or never say when the thing happened, and both are dropped rather than guessed
at.
"""

import argparse
import collections
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.sources import guardian  # noqa: E402

CACHE = os.environ.get("GUARDIAN_CACHE", os.path.expanduser("~/.cache/guardian"))


def to_candidate(row):
    """One article as a reviewable candidate, never as a finished question."""
    event_date = row.get("eventDate")
    if not event_date:
        return None

    y, m, d = event_date[:4], event_date[5:7], event_date[8:10]
    return {
        "sport": "news",
        "league": row.get("section") or "Sport",
        "reason": "narrative_event",
        "notabilityScore": 70,
        "gameId": f"news-{row['url'].rsplit('/', 1)[-1][:60]}",
        "gameDate": event_date,
        "year": int(y),
        "mmdd": f"{m}-{d}",
        "title": row["headline"],
        "facts": {
            # The cited sentence, verbatim. Nothing downstream may state
            # anything this does not.
            "headline": row["headline"],
            "summary": row.get("summary"),
            "publishedAt": row.get("publishedAt"),
        },
        # Never `draft`: a narrative candidate is not a question until somebody
        # has read the sentence and written one.
        "status": "needs_review",
        "sourceName": row["sourceName"],
        "sourceDatasetRef": row["sourceDatasetRef"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", required=True, help="yyyy-mm-dd")
    ap.add_argument("--to", dest="end", required=True, help="yyyy-mm-dd")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=CACHE)
    args = ap.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if not os.environ.get("GUARDIAN_API_KEY"):
        print("warning: no GUARDIAN_API_KEY set — using the shared test key, "
              "which is rate limited and unusable for a real backfill")

    rows = guardian.fetch_range(start, end, args.cache)
    candidates = [c for c in (to_candidate(r) for r in rows) if c]

    with open(args.out, "w") as f:
        for c in candidates:
            f.write(json.dumps(c, default=str) + "\n")

    days = (end - start).days + 1
    print(f"days scanned      : {days}")
    print(f"articles kept     : {len(rows)}")
    print(f"dates resolved    : {len(candidates)}")
    print(f"dropped, no date  : {len(rows) - len(candidates)}")
    print(f"calendar dates    : {len({c['mmdd'] for c in candidates})}")
    print("by section        :",
          dict(collections.Counter(c['league'] for c in candidates)))
    print(f"\nwrote {args.out} — all `needs_review`, none are questions yet")


if __name__ == "__main__":
    main()
