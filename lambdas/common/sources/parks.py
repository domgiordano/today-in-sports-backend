"""
Ballparks, and where they were.

Retrosheet's `parkcode.txt` lists every park ever used for a major league game —
260 of them — with a city, a state, and the dates it was in service. Game logs
carry the park code, so every game in the corpus already knows where it was
played. What was missing was a coordinate.

**The coordinate is the city's, not the park's, and that is deliberate.**
Map questions score on distance with full credit inside 50km, so the few
kilometres between a city centroid and the actual diamond are below what the
grading can distinguish. Geocoding "Brooklyn, NY" is a lookup anyone can
repeat; matching "Polo Grounds V" to the right one of five identically-named
parks in a coordinate database is fuzzy string work that fails silently and
puts a pin in the wrong borough. The precision that would be gained is not
precision the game uses.

Which parks make a question is the more interesting rule, and it is in
`is_defunct`. A game at Fenway is not a geography question — anyone who knows
the Red Sox knows Boston. A game at Ebbets Field is, because the franchise
moved 3,900km and the answer cannot be derived from where the club plays now.
So only parks that have closed are asked about.
"""

import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from lambdas.common.logger import get_logger

log = get_logger(__file__)

BASE = "https://www.retrosheet.org"
UA = "today-in-sports/0.1 (+https://todayinsports.app)"

SOURCE_NAME = "Retrosheet"
PARK_FILE = "parkcode.txt"

NOMINATIM = "https://nominatim.openstreetmap.org/search"

# Parks that closed before this still count as defunct; the cutoff exists only
# so a park recorded with an END date in the future is not treated as closed.
TODAY = date.today()


class SourceError(Exception):
    pass


def _fetch(url, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, os.path.basename(url))
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(local, "wb") as f:
            f.write(r.read())
    except Exception as e:
        raise SourceError(f"could not fetch {url}: {e}") from e
    time.sleep(0.5)  # a free volunteer-run archive
    return local


def _parse_mdy(value):
    """Retrosheet dates are mm/dd/yyyy, and blank means still in use."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        m, d, y = value.split("/")
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def load_parks(cache_dir):
    """
    Every park Retrosheet knows, keyed by its code.

    `aka` is kept because several parks are far better known by a later name —
    Enron Field and Minute Maid Park are the same building — and a result
    screen naming the one nobody uses reads like an error.
    """
    local = _fetch(f"{BASE}/{PARK_FILE}", cache_dir)
    parks = {}
    with open(local, newline="", encoding="latin-1") as f:
        for row in csv.DictReader(f):
            park_id = (row.get("PARKID") or "").strip()
            if not park_id:
                continue
            parks[park_id] = {
                "parkId": park_id,
                "name": (row.get("NAME") or "").strip(),
                "aka": [a.strip() for a in (row.get("AKA") or "").split(";")
                        if a.strip()],
                "city": (row.get("CITY") or "").strip(),
                "state": (row.get("STATE") or "").strip(),
                "start": _parse_mdy(row.get("START")),
                "end": _parse_mdy(row.get("END")),
                "league": (row.get("LEAGUE") or "").strip(),
            }
    log.info(f"parks: {len(parks)} from {PARK_FILE}")
    return parks


def is_defunct(park):
    """
    Has this park closed?

    The whole rule for whether a park is worth asking about. An open park is
    answerable from the club's current city, which makes it a question about
    the present rather than about the day it is anchored to.
    """
    end = park.get("end")
    return bool(end and end <= TODAY)


# ---------------------------------------------------------------- geocoding

# Retrosheet's STATE column is not two-letter codes throughout. Canada is
# `ONT`/`QUE`, Japan is `JAP`, and England and Australia are spelled out. Every
# non-US value in parkcode.txt is listed here rather than pattern-matched,
# because guessing produced "Toronto, ONT, USA" — a place that does not exist,
# which Nominatim correctly refused to resolve, silently dropping every
# international park.
FOREIGN_STATES = {
    "ONT": "Canada",
    "QUE": "Canada",
    "JAP": "Japan",
    "MX": "Mexico",
    "PR": "Puerto Rico",
    "England": "United Kingdom",
    "Australia": "Australia",
}


def _city_key(park):
    """
    What gets geocoded.

    City and state together, because there is a Kansas City in two states and
    a Columbus in several. The country is appended so a state code that means
    nothing to a geocoder still lands in the right country.
    """
    city, state = park.get("city"), park.get("state")
    if not (city and state):
        return None
    country = FOREIGN_STATES.get(state)
    if country:
        # The state code is not a real subdivision name in these cases, so
        # sending it would only confuse the lookup.
        return f"{city}, {country}"
    return f"{city}, {state}, USA"


def geocode(query, cache_path=None, session_cache=None):
    """
    One place to one coordinate, via Nominatim.

    Cached on disk between runs because the whole set is a few hundred lookups
    against a free service with a one-request-per-second policy, and re-running
    the corpus build should not re-ask for something that cannot have changed.

    Returns None rather than a guess when nothing matches. A park with no
    coordinate produces no question, which is the same standard every other
    source here is held to.
    """
    if session_cache is not None and query in session_cache:
        return session_cache[query]

    params = urllib.parse.urlencode({
        "q": query, "format": "json", "limit": 1,
    })
    req = urllib.request.Request(f"{NOMINATIM}?{params}",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.load(r)
    except Exception as exc:  # noqa: BLE001 - one failed lookup is not fatal
        log.warning(f"geocode failed for {query}: {exc}")
        payload = []

    time.sleep(1.1)  # Nominatim asks for at most one request a second

    result = None
    if payload:
        try:
            result = {"lat": float(payload[0]["lat"]),
                      "lng": float(payload[0]["lon"])}
        except (KeyError, TypeError, ValueError):
            result = None

    if session_cache is not None:
        session_cache[query] = result
    if cache_path:
        _write_cache(cache_path, session_cache or {query: result})
    return result


def _write_cache(path, cache):
    with open(path, "w") as f:
        json.dump({k: v for k, v in cache.items()}, f, indent=1, sort_keys=True)


def load_cache(path):
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path) as f:
            return json.load(f)
    return {}


def build_index(parks, coords):
    """
    Park code to the answer a map question needs.

    Only defunct parks with a resolved coordinate survive. Both filters matter:
    an open park is a question about the present, and a park with no coordinate
    has no answer.
    """
    index = {}
    for park_id, park in parks.items():
        if not is_defunct(park):
            continue
        key = _city_key(park)
        point = coords.get(key) if key else None
        if not point:
            continue
        index[park_id] = {
            "parkId": park_id,
            "name": park["name"],
            "aka": park["aka"],
            "city": park["city"],
            "state": park["state"],
            "lat": point["lat"],
            "lng": point["lng"],
            "closed": park["end"].isoformat() if park.get("end") else None,
        }
    return index
