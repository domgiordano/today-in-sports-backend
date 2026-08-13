"""
Formula One source adapter, reading the f1db release dump.

Chosen over an API deliberately. Ergast was the standard free F1 API and it shut
down at the end of 2024 — its CSV download now redirects to a homepage. f1db is
a versioned, CC-BY-4.0 GitHub release covering 1950 to the present, which is a
file rather than an endpoint and therefore cannot be switched off underneath us.
`api.jolpi.ca` remains a live Ergast-compatible option for recent seasons only.

The dump is unusually well suited to this project: `driversChampionshipDecider`
is a field on the race, so title-deciding races need no inference at all, and
results carry `polePosition` and `gridPositionNumber` directly.

Licence: CC-BY-4.0 — attribution required, see ATTRIBUTION.
"""

import csv
import os
import urllib.request
import zipfile

RELEASE_API = "https://api.github.com/repos/f1db/f1db/releases/latest"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"
SOURCE_NAME = "f1db"

ATTRIBUTION = (
    "Formula One data from f1db (https://github.com/f1db/f1db), "
    "licensed CC-BY-4.0."
)


class SourceError(Exception):
    pass


def download(cache_dir):
    """Fetch the newest f1db CSV release once; later calls reuse the local copy."""
    os.makedirs(cache_dir, exist_ok=True)
    marker = os.path.join(cache_dir, "f1db-races.csv")
    if os.path.exists(marker):
        return cache_dir

    import json
    req = urllib.request.Request(RELEASE_API, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        release = json.load(r)

    asset = next((a for a in release.get("assets", [])
                  if a["name"] == "f1db-csv.zip"), None)
    if not asset:
        raise SourceError("f1db-csv.zip not present in the latest release")

    local_zip = os.path.join(cache_dir, "f1db-csv.zip")
    req = urllib.request.Request(asset["browser_download_url"],
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r, open(local_zip, "wb") as f:
        f.write(r.read())
    with zipfile.ZipFile(local_zip) as z:
        z.extractall(cache_dir)
    return cache_dir


def _read(cache_dir, name):
    path = os.path.join(cache_dir, name)
    if not os.path.exists(path):
        raise SourceError(f"missing {name} — run download() first")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _bool(v):
    return str(v).strip().lower() == "true"


def load_races(cache_dir):
    """
    Races with their results attached, in chronological order.

    Career win counts are computed here rather than in a detector: "first career
    win" is only knowable by walking the whole history in order, which is a
    property of the corpus, not of a single race.
    """
    download(cache_dir)

    drivers = {d["id"]: (d.get("fullName") or d.get("name") or d["id"])
               for d in _read(cache_dir, "f1db-drivers.csv")}
    constructors = {c["id"]: (c.get("fullName") or c.get("name") or c["id"])
                    for c in _read(cache_dir, "f1db-constructors.csv")}
    grands_prix = {g["id"]: (g.get("fullName") or g.get("name") or g["id"])
                   for g in _read(cache_dir, "f1db-grands-prix.csv")}

    results_by_race = {}
    for r in _read(cache_dir, "f1db-races-race-results.csv"):
        results_by_race.setdefault(r["raceId"], []).append(r)

    races = [r for r in _read(cache_dir, "f1db-races.csv") if r.get("date")]
    races.sort(key=lambda r: r["date"])

    wins_so_far = {}
    starts_so_far = {}
    out = []

    for race in races:
        results = results_by_race.get(race["id"], [])
        results.sort(key=lambda r: _int(r.get("positionDisplayOrder")) or 999)

        winner_row = next(
            (r for r in results if _int(r.get("positionNumber")) == 1), None)

        starters = {r["driverId"] for r in results}
        for did in starters:
            starts_so_far[did] = starts_so_far.get(did, 0) + 1

        winner = None
        if winner_row:
            did = winner_row["driverId"]
            wins_so_far[did] = wins_so_far.get(did, 0) + 1
            winner = {
                "driverId": did,
                "driver": drivers.get(did, did),
                "constructorId": winner_row.get("constructorId"),
                "constructor": constructors.get(winner_row.get("constructorId"),
                                                winner_row.get("constructorId")),
                "gridPosition": _int(winner_row.get("gridPositionNumber")),
                "polePosition": _bool(winner_row.get("polePosition")),
                "careerWins": wins_so_far[did],
                "careerStarts": starts_so_far.get(did, 0),
            }

        podium = []
        for pos in (1, 2, 3):
            row = next((r for r in results
                        if _int(r.get("positionNumber")) == pos), None)
            if row:
                podium.append(drivers.get(row["driverId"], row["driverId"]))

        out.append({
            "sport": "f1",
            "league": "Formula One",
            "leagueId": "F1",
            "gameId": race["id"],
            "gameDate": race["date"],
            "year": _int(race.get("year")),
            "round": _int(race.get("round")),
            "officialName": race.get("officialName"),
            "grandPrix": grands_prix.get(race.get("grandPrixId"),
                                         race.get("grandPrixId")),
            "circuitId": race.get("circuitId"),
            "laps": _int(race.get("laps")),
            "championshipDecider": _bool(race.get("driversChampionshipDecider")),
            "constructorsDecider": _bool(race.get("constructorsChampionshipDecider")),
            "winner": winner,
            "podium": podium,
            "finishers": len([r for r in results
                              if _int(r.get("positionNumber")) is not None]),
            "entries": len(results),
            "sourceName": SOURCE_NAME,
            "sourceDatasetRef": (
                "https://github.com/f1db/f1db/releases/latest"
                f"#race={race['id']}"
            ),
        })

    return out
