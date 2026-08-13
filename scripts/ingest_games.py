#!/usr/bin/env python3
"""
Ingest MLB history from Retrosheet into the games table.

Runs locally, never in a Lambda. One-time extraction of immutable data: once a
season is ingested and its raw file archived to S3, nothing here needs to run
again for that season, and the upstream source can disappear without affecting
the product.

    python scripts/ingest_games.py --from 1871 --to 2026
    python scripts/ingest_games.py --from 1991 --to 1991 --dry-run
    python scripts/ingest_games.py --from 1871 --to 2026 --resume

Resumable by season: each completed season writes a checkpoint to the
source-runs table, and --resume skips seasons already marked done. A multi-decade
backfill can be interrupted and restarted without refetching.

Retrosheet attribution (licence requirement) is printed on every run.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.sources import retrosheet as rs  # noqa: E402

CACHE_DIR = os.environ.get("RETROSHEET_CACHE", os.path.expanduser("~/.cache/retrosheet"))
GAMES_TABLE = os.environ.get("GAMES_TABLE_NAME", "today-in-sports-games")
RUNS_TABLE = os.environ.get("SOURCE_RUNS_TABLE_NAME", "today-in-sports-source-runs")
RAW_BUCKET = os.environ.get("RAW_ARCHIVE_BUCKET")

# Retrosheet's earliest game logs. Anything before this simply does not exist.
EARLIEST = 1871


def _boto():
    import boto3
    return boto3.resource("dynamodb"), boto3.client("s3")


def archive_raw(s3, year, local_paths):
    """
    Push the untouched source file to S3 before anything parses it.

    This archive, not retrosheet.org, is the source of record once ingestion
    completes. Skipped when RAW_ARCHIVE_BUCKET is unset so the script stays
    runnable without AWS.
    """
    if not RAW_BUCKET or s3 is None:
        return []
    keys = []
    for p in local_paths:
        if not os.path.exists(p):
            continue
        key = f"retrosheet/{year}/{os.path.basename(p)}"
        s3.upload_file(p, RAW_BUCKET, key)
        keys.append(key)
    return keys


def write_games(table, games):
    """Batch write, keyed so a re-run overwrites rather than duplicates."""
    with table.batch_writer(overwrite_by_pkeys=["sportSeason", "gameDateId"]) as batch:
        for g in games:
            item = {
                "sportSeason": f"{g['sport']}#{g['season']}",
                "gameDateId": f"{g['gameDate']}#{g['gameId']}",
                **{k: v for k, v in g.items() if v is not None},
            }
            batch.put_item(Item=_clean(item))


def _clean(obj):
    """DynamoDB rejects empty strings and floats; normalise before writing."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if v != "" and v is not None}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, float):
        from decimal import Decimal
        return Decimal(str(obj))
    return obj


def completed_seasons(runs_table):
    if runs_table is None:
        return set()
    done = set()
    resp = runs_table.scan(ProjectionExpression="runId, #s",
                           ExpressionAttributeNames={"#s": "status"})
    for item in resp.get("Items", []):
        if item.get("status") == "complete" and item["runId"].startswith("mlb-"):
            done.add(int(item["runId"].split("-")[1]))
    return done


def checkpoint(runs_table, year, count, keys, status):
    if runs_table is None:
        return
    runs_table.put_item(Item=_clean({
        "runId": f"mlb-{year}",
        "source": "retrosheet",
        "season": year,
        "gamesIngested": count,
        "rawKeys": keys,
        "status": status,
        "finishedAt": datetime.now(timezone.utc).isoformat(),
    }))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=EARLIEST)
    ap.add_argument("--to", dest="end", type=int, default=datetime.now(timezone.utc).year)
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, write nothing")
    ap.add_argument("--resume", action="store_true",
                    help="skip seasons already checkpointed complete")
    ap.add_argument("--out", help="in dry-run, also dump normalized games to this JSON file")
    args = ap.parse_args()

    print(rs.ATTRIBUTION)
    print()

    games_table = runs_table = s3 = None
    if not args.dry_run:
        dynamo, s3 = _boto()
        games_table = dynamo.Table(GAMES_TABLE)
        runs_table = dynamo.Table(RUNS_TABLE)

    skip = completed_seasons(runs_table) if (args.resume and runs_table) else set()
    if skip:
        print(f"resume: skipping {len(skip)} completed seasons\n")

    total = 0
    collected = []
    for year in range(max(args.start, EARLIEST), args.end + 1):
        if year in skip:
            continue
        try:
            regular = rs.fetch_season(year, CACHE_DIR)
        except rs.SourceError as e:
            # Not every year has a log — expect gaps, do not abort the backfill.
            print(f"  {year}: unavailable ({e})")
            continue

        post = rs.fetch_postseason(year, CACHE_DIR)
        games = regular + post
        total += len(games)

        if args.dry_run:
            collected.extend(games)
            print(f"  {year}: {len(regular):5d} regular + {len(post):3d} postseason "
                  f"= {len(games):5d} games")
            continue

        paths = [os.path.join(CACHE_DIR, f"gl{year}.zip")] + [
            os.path.join(CACHE_DIR, f) for f in rs.POSTSEASON_FILES.values()
        ]
        keys = archive_raw(s3, year, paths)
        write_games(games_table, games)
        checkpoint(runs_table, year, len(games), keys, "complete")
        print(f"  {year}: {len(games):5d} games written")

    print(f"\ntotal games: {total}")

    if args.dry_run and args.out:
        with open(args.out, "w") as f:
            json.dump(collected, f, default=str)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
