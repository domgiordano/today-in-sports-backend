#!/usr/bin/env python3
"""
Ingest NBA history from balldontlie.

    python scripts/ingest_nba.py --from 1980 --to 2025 --out nba_games.json

Requires an API key: set BALLDONTLIE_API_KEY, or store it at
/today-in-sports/balldontlie/api-key in SSM.

Fetches a season per cursor walk rather than a request per date, and throttles
hard — the free tier is metered per minute. Checkpoints after every season so a
long backfill survives interruption.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.sources import balldontlie as bdl  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1980)
    ap.add_argument("--to", dest="end", type=int, default=2025)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        bdl.api_key()
    except bdl.MissingCredentialError as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 1

    existing = []
    if os.path.exists(args.out):
        try:
            existing = json.load(open(args.out))
        except Exception:
            existing = []
    have = {g["gameId"] for g in existing}
    all_games = list(existing)
    print(f"resuming with {len(existing)} games", flush=True)

    for season in range(args.start, args.end + 1):
        try:
            games = bdl.fetch_season(season)
        except bdl.SourceError as e:
            print(f"  {season}: failed ({e})", flush=True)
            continue

        fresh = [g for g in games if g["gameId"] not in have]
        for g in fresh:
            have.add(g["gameId"])
        all_games.extend(fresh)
        print(f"  {season}-{str(season + 1)[2:]}: {len(fresh):5d} games "
              f"(total {len(all_games)})", flush=True)

        with open(args.out, "w") as f:
            json.dump(all_games, f, default=str)

    print(f"\ntotal NBA games: {len(all_games)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
