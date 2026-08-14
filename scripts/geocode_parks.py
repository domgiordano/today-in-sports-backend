#!/usr/bin/env python3
"""
Resolve every defunct ballpark to a coordinate, once.

    python scripts/geocode_parks.py --out parks.json

Nominatim asks for at most one request a second, so this takes a couple of
minutes the first time and nothing at all afterwards — the raw lookups are
cached beside the output and re-read on every subsequent run.

Deliberately a script and not part of the corpus build. Geocoding is a network
call to somebody else's free service against data that changes roughly never;
running it on every build would be rude and slow, and running it inside a
Lambda would put a third-party outage on the request path.

The output is an input to `load_corpus.py --parks`. Nothing here writes to
DynamoDB.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.sources import parks as parks_source  # noqa: E402

CACHE = os.environ.get("RETROSHEET_CACHE", os.path.expanduser("~/.cache/retrosheet"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="park index, for load_corpus.py")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--geocode-cache",
                    help="raw place lookups (defaults to <out>.geocode.json)")
    args = ap.parse_args()

    geocode_cache_path = args.geocode_cache or f"{args.out}.geocode.json"

    parks = parks_source.load_parks(args.cache)
    defunct = {k: p for k, p in parks.items()
               if parks_source.is_defunct(p)
               and parks_source.served_long_enough(p)}
    print(f"parks known       : {len(parks)}")
    print(f"defunct, 2+ seasons: {len(defunct)}")

    # Geocode distinct places, not parks. Five Polo Grounds share one city, and
    # asking five times for the same answer is five times the imposition on a
    # free service for no additional information.
    places = {}
    for park in defunct.values():
        key = parks_source._city_key(park)
        if key:
            places.setdefault(key, []).append(park["parkId"])
    print(f"distinct places   : {len(places)}")

    coords = parks_source.load_cache(geocode_cache_path)
    todo = [p for p in places if p not in coords]
    print(f"already cached    : {len(places) - len(todo)}")
    print(f"to look up        : {len(todo)}")

    for i, place in enumerate(sorted(todo), 1):
        coords[place] = parks_source.geocode(place, session_cache=coords)
        found = "ok" if coords[place] else "not found"
        print(f"  [{i}/{len(todo)}] {place}: {found}", flush=True)
        # Written after every lookup, so an interrupted run resumes rather
        # than starting the whole polite crawl again.
        parks_source._write_cache(geocode_cache_path, coords)

    parks_source._write_cache(geocode_cache_path, coords)

    index = parks_source.build_index(parks, coords)
    with open(args.out, "w") as f:
        json.dump(index, f, indent=1, sort_keys=True)

    # Only places this run actually needed. Reading the whole cache would keep
    # reporting failures for keys nobody asks for any more — which is exactly
    # what happened when the country codes were fixed and "Toronto, ONT, USA"
    # stayed in the cache as a permanent phantom failure.
    unresolved = sorted(p for p in places if not coords.get(p))
    print(f"\nparks with a coordinate: {len(index)}")
    if unresolved:
        print(f"places unresolved      : {len(unresolved)}")
        for place in unresolved[:10]:
            print(f"    {place}")
        print("  (those parks produce no questions, rather than a guessed pin)")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
