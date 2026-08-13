#!/usr/bin/env python3
"""
Ingest NHL history from api-web.nhle.com.

    python scripts/ingest_nhl.py --from 1950 --to 2024 --out nhl_games.json

Fetches a week per request rather than a day, so a season costs ~44 requests
instead of ~365. Writes incrementally so a long backfill can be interrupted and
resumed without losing what it already has.

This is the one source in the corpus with no downloadable dump, and the league
has already retired one API host. Archive the output.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.sources import nhl  # noqa: E402

CACHE = os.environ.get("NHL_CACHE", os.path.expanduser("~/.cache/nhl"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1950)
    ap.add_argument("--to", dest="end", type=int, default=2024)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # Resume from whatever a previous run already wrote.
    existing = []
    if os.path.exists(args.out):
        try:
            existing = json.load(open(args.out))
        except Exception:
            existing = []
    have = {g["gameId"] for g in existing}
    print(f"resuming with {len(existing)} games already on disk", flush=True)

    all_games = list(existing)
    for year in range(args.start, args.end + 1):
        try:
            season = nhl.fetch_season(year, CACHE)
        except Exception as e:
            print(f"  {year}: failed ({e})", flush=True)
            continue

        fresh = [g for g in season if g["gameId"] not in have]
        for g in fresh:
            have.add(g["gameId"])
        all_games.extend(fresh)

        print(f"  {year}-{str(year + 1)[2:]}: {len(fresh):5d} games "
              f"(total {len(all_games)})", flush=True)

        # Checkpoint every season so an interrupted run keeps its progress.
        with open(args.out, "w") as f:
            json.dump(all_games, f, default=str)

    print(f"\ntotal NHL games: {len(all_games)}", flush=True)


if __name__ == "__main__":
    main()
