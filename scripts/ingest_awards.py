#!/usr/bin/env python3
"""
Ingest MLB award winners.

    python scripts/ingest_awards.py --from 1931 --to 2025 --out awards.jsonl

Awards are date-anchored events in their own right - the announcement has a
date - and they are also where career accolades come from, which is what turns
"who is this?" into "this three-time Cy Young winner".
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.sources import mlb_awards  # noqa: E402

CACHE = os.environ.get("AWARDS_CACHE", os.path.expanduser("~/.cache/mlb-awards"))


def to_event(row):
    """One award announcement as a corpus event."""
    date = row.get("date")
    if not date or len(date) < 10:
        return None
    y, m, d = date[:4], date[5:7], date[8:10]

    return {
        "sport": "mlb",
        "league": "MLB",
        "leagueId": "MLB",
        "isNegroLeagues": False,
        "reason": "award_winner",
        "notabilityScore": 88,
        "gameId": f"award-{row['awardId']}-{row['season']}-{row.get('playerId')}",
        "gameDate": date[:10],
        "year": int(y),
        "mmdd": f"{m}-{d}",
        "title": f"{row['player']} won the {row['awardShort']}",
        "facts": {
            "player": row["player"],
            "award": row["awardShort"],
            "awardFull": row["awardName"],
            "awardFamily": row["family"],
            "season": row["season"],
            "team": row.get("team"),
        },
        "sourceName": row["sourceName"],
        "sourceDatasetRef": row["sourceDatasetRef"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1931)
    ap.add_argument("--to", dest="end", type=int, default=2025)
    ap.add_argument("--out", required=True)
    ap.add_argument("--accolades", help="also write the career accolade index")
    ap.add_argument("--cache", default=CACHE)
    args = ap.parse_args()

    rows = mlb_awards.fetch_range(args.start, args.end, args.cache)
    print(f"award rows: {len(rows)}")

    events = [e for e in (to_event(r) for r in rows) if e]
    with open(args.out, "w") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")

    index = mlb_awards.accolade_index(rows)
    if args.accolades:
        with open(args.accolades, "w") as f:
            json.dump(index, f, indent=1, sort_keys=True)
        print(f"wrote {args.accolades} ({len(index)} players)")

    multi = {n: c for n, c in index.items() if max(c.values()) > 1}
    print(f"events: {len(events)}")
    print(f"  calendar dates: {len({e['mmdd'] for e in events})}/366")
    print(f"  players with an honour: {len(index)}")
    print(f"  multiple winners: {len(multi)}")
    print("  by award:",
          dict(collections.Counter(e['facts']['award'] for e in events)))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
