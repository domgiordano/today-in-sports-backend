"""
MLB awards, from the MLB Stats API.

This was very nearly hand-curated on the assumption that award results were not
available as structured data. They are: `statsapi.mlb.com/api/v1/awards` lists
682 awards and the recipients endpoint returns winners back to 1931, with a
date attached.

The date matters twice over. It makes an award a date-anchored event like any
other - "on this day, the MVP was announced" - and it means the accolade can be
attributed to the right day rather than only to a season.

The second use is the more valuable one: counting a player's awards across
their career turns "who is this?" into "this three-time Cy Young winner", which
is the difference between a clue that narrows the field and one that does not.
"""

import json
import os
import time
import urllib.error
import urllib.request

from lambdas.common.logger import get_logger

log = get_logger(__file__)

BASE = "https://statsapi.mlb.com/api/v1"
UA = "today-in-sports/0.1 (+https://todayinsports.app)"

SOURCE_NAME = "MLB Stats API"

# The awards a fan recognises by name. Deliberately not all 682: the catalogue
# is mostly retired uniform numbers and per-club honours, and a question about
# the Astros Rookie of the Year is a question about nothing.
# `label` spells the league out. The short forms read fine in a table and
# badly in a prompt, and "the AL MVP" is indistinguishable from an unresolved
# Retrosheet team code to the validator that exists to catch those.
AWARDS = {
    "ALMVP": {"label": "American League Most Valuable Player award",
              "short": "AL MVP"},
    "NLMVP": {"label": "National League Most Valuable Player award",
              "short": "NL MVP"},
    "ALCY": {"label": "American League Cy Young award", "short": "AL Cy Young"},
    "NLCY": {"label": "National League Cy Young award", "short": "NL Cy Young"},
    "ALROY": {"label": "American League Rookie of the Year award",
              "short": "AL Rookie of the Year"},
    "NLROY": {"label": "National League Rookie of the Year award",
              "short": "NL Rookie of the Year"},
}

# Awards that mean the same thing across leagues, for counting a career total.
# A three-time MVP is a three-time MVP whichever league he was in.
FAMILY = {
    "ALMVP": "MVP", "NLMVP": "MVP",
    "ALCY": "Cy Young", "NLCY": "Cy Young",
    "ALROY": "Rookie of the Year", "NLROY": "Rookie of the Year",
}


class SourceError(Exception):
    pass


def _get(path, cache_dir=None):
    """Fetch JSON, caching to disk so a re-run costs nothing."""
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        local = os.path.join(cache_dir, path.replace("/", "_").replace("?", "_")
                             .replace("&", "_").replace("=", "-") + ".json")
        if os.path.exists(local) and os.path.getsize(local) > 0:
            with open(local) as f:
                return json.load(f)

    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise SourceError(f"could not fetch {path}: {e}") from e
    except Exception as e:
        raise SourceError(f"could not fetch {path}: {e}") from e

    if cache_dir:
        with open(local, "w") as f:
            json.dump(payload, f)
    time.sleep(0.2)  # a free public API; do not hammer it
    return payload


def recipients(award_id, season, cache_dir=None):
    """
    Winners of one award in one season.

    Returns a list because a tie is possible and has happened - 1979 NL MVP
    was shared. Treating it as a single winner would silently drop somebody's
    award.
    """
    payload = _get(f"awards/{award_id}/recipients?season={season}", cache_dir)
    out = []
    for row in payload.get("awards") or []:
        player = row.get("player") or {}
        name = player.get("nameFirstLast") or player.get("fullName")
        if not name:
            continue
        out.append({
            "awardId": award_id,
            "awardName": AWARDS.get(award_id, {}).get("label", award_id),
            "awardShort": AWARDS.get(award_id, {}).get("short", award_id),
            "family": FAMILY.get(award_id, award_id),
            "season": int(season),
            "playerId": player.get("id"),
            "player": name,
            "date": row.get("date"),
            "team": (row.get("team") or {}).get("name"),
            "sourceName": SOURCE_NAME,
            "sourceDatasetRef": (
                f"https://statsapi.mlb.com/api/v1/awards/{award_id}"
                f"/recipients?season={season}"),
        })
    return out


def fetch_range(start_year, end_year, cache_dir=None, award_ids=None):
    """Every recognised award across a span of seasons."""
    award_ids = award_ids or list(AWARDS)
    out = []
    for season in range(start_year, end_year + 1):
        for award_id in award_ids:
            try:
                out.extend(recipients(award_id, season, cache_dir))
            except SourceError as exc:
                log.warning(f"{award_id} {season}: {exc}")
    log.info(f"awards: {len(out)} across {start_year}-{end_year}")
    return out


def accolade_index(awards):
    """
    Player name to their career honours.

    The shape a clue needs: `{"Randy Johnson": {"Cy Young": 5, ...}}`. Keyed on
    name rather than id because the corpus's other sources identify players by
    name and there is no shared id between Retrosheet and the Stats API.
    """
    index = {}
    for row in awards:
        name = row.get("player")
        if not name:
            continue
        family = row.get("family") or row.get("awardName")
        counts = index.setdefault(name, {})
        counts[family] = counts.get(family, 0) + 1
    return index


def describe_accolades(counts):
    """
    Career honours as a phrase, most impressive first.

    "three-time Cy Young winner" reads as a clue; "Cy Young: 3" reads as a
    database row.
    """
    if not counts:
        return None

    words = {1: "one-time", 2: "two-time", 3: "three-time", 4: "four-time",
             5: "five-time", 6: "six-time", 7: "seven-time"}

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    family, n = ranked[0]
    if n == 1:
        return f"He won the {family} at least once."
    return f"He was a {words.get(n, f'{n}-time')} {family} winner."
