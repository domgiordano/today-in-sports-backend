#!/usr/bin/env python3
"""
Ingest Formula One history from the f1db dump.

    python scripts/ingest_f1.py --out f1_events.jsonl

Written because the F1 events already in the corpus came from an ad-hoc run and
carry no `circuitId`, which is what a map question needs to know where a race
was held. A source that cannot be re-run is a source that cannot be corrected.

f1db publishes a versioned CSV release with every race, result and circuit -
including latitude and longitude - so this needs no scraping and no geocoding.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.notability import f1 as f1_nb     # noqa: E402
from lambdas.common.sources import f1db               # noqa: E402

CACHE = os.environ.get("F1DB_CACHE", os.path.expanduser("~/.cache/f1db"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="JSONL, one event per line")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--skip-download", action="store_true",
                    help="reuse an already-extracted dump")
    args = ap.parse_args()

    if not args.skip_download:
        print("downloading the f1db release...")
        f1db.download(args.cache)

    races = f1db.load_races(args.cache)
    circuits = f1db.load_circuits(args.cache)
    print(f"races: {len(races)}, circuits with coordinates: {len(circuits)}")

    events = f1_nb.run(races)
    with_circuit = sum(1 for e in events
                       if (e.get("facts") or {}).get("circuitId") in circuits)

    with open(args.out, "w") as f:
        for e in events:
            f.write(json.dumps(e, default=str) + "\n")

    print(f"events: {len(events)}")
    print(f"  locatable on a map: {with_circuit}")
    print(f"  calendar dates: {len({e['mmdd'] for e in events})}/366")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
