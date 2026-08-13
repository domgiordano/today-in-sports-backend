"""
NHL source adapter.

The only source in the corpus with no downloadable dump, and the one that has
already broken once — the league retired `statsapi.web.nhl.com` and replaced it
with `api-web.nhle.com`. So this is the source whose raw payloads matter most to
archive at ingestion time: once archived, the corpus survives the next move.

Coverage reaches **1918**, contrary to an early assumption of ~1980. That guess
came from probing single dates: 1972-09-28 was the Summit Series rather than an
NHL fixture, and a six-team league simply idled most days. Sampling six dates a
year shows games in every season from 1918 on.

Two endpoints are used:
  * /v1/schedule/{date}  — a full week per request, scores included, so a season
                           costs ~52 requests rather than ~365.
  * records.nhl.com/site/api/franchise — era-aware franchise names, so a 1925
                           game reads "Hamilton Tigers" rather than an abbrev.
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date as _date, timedelta

API = "https://api-web.nhle.com/v1"
RECORDS = "https://records.nhl.com/site/api"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"

SOURCE_NAME = "nhl-api"
THROTTLE_SECONDS = 0.25
MAX_RETRIES = 4

# 1 preseason, 2 regular season, 3 playoffs.
GAME_TYPE_LABEL = {1: "Preseason", 2: "Regular Season", 3: "Playoffs"}


class SourceError(Exception):
    pass


def _get(url):
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            time.sleep(THROTTLE_SECONDS)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise SourceError(f"HTTP {e.code} for {url}") from e
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise SourceError(f"{type(e).__name__} for {url}") from e
    raise SourceError(f"exhausted retries for {url}")


# ------------------------------------------------------------- franchises

def load_franchises(cache_dir=None):
    """
    Map an abbreviation plus a season to the franchise name in use then.

    Abbreviations are reused across history — ATL was the Flames and later the
    Thrashers — so the season window is what disambiguates.
    """
    cached = os.path.join(cache_dir, "nhl_franchises.json") if cache_dir else None
    if cached and os.path.exists(cached):
        with open(cached) as f:
            data = json.load(f)
    else:
        data = _get(f"{RECORDS}/franchise")
        if cached:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cached, "w") as f:
                json.dump(data, f)

    out = {}
    for fr in data.get("data", []):
        abbrev = fr.get("teamAbbrev")
        if not abbrev:
            continue
        out.setdefault(abbrev, []).append({
            "name": fr.get("fullName") or abbrev,
            "first": fr.get("firstSeasonId") or 0,
            "last": fr.get("lastSeasonId"),
        })
    return out


def team_name(franchises, abbrev, season):
    """season is the API's 19931994 form."""
    options = franchises.get(abbrev)
    if not options:
        return abbrev
    for o in options:
        if season < (o["first"] or 0):
            continue
        if o["last"] and season > o["last"]:
            continue
        return o["name"]
    return options[-1]["name"]


# -------------------------------------------------------------- normalize

def normalize(game, franchises, source_url, day_date=None):
    """
    `day_date` is the date from the enclosing gameWeek entry.

    The score endpoint puts `gameDate` on the game itself, but the schedule
    endpoint carries it on the day wrapper and leaves it absent on the game — so
    the caller supplies it and this falls back to it.
    """
    season = game.get("season") or 0
    away, home = game.get("awayTeam") or {}, game.get("homeTeam") or {}
    a_score, h_score = away.get("score"), home.get("score")

    series = game.get("seriesStatus") or {}
    period = game.get("periodDescriptor") or {}

    def side(t, score, opp_score):
        abbrev = t.get("abbrev")
        return {
            "team": team_name(franchises, abbrev, season),
            "teamId": abbrev,
            "league": "NHL",
            "leagueId": "NHL",
            "score": score,
            "shots": t.get("sog"),
            "isWinner": (score is not None and opp_score is not None
                         and score > opp_score),
        }

    game_type = game.get("gameType")
    return {
        "sport": "nhl",
        "gameId": game.get("id"),
        "gameDate": game.get("gameDate") or day_date,
        "season": season,
        "gameType": game_type,
        "status": game.get("gameState"),
        "seriesDescription": (series.get("seriesTitle")
                              or GAME_TYPE_LABEL.get(game_type, "")),
        "seriesRound": series.get("round"),
        "seriesAbbrev": series.get("seriesAbbrev"),
        "seriesGameNumber": series.get("gameNumberOfSeries"),
        "neededToWin": series.get("neededToWin"),
        "topSeed": series.get("topSeedTeamAbbrev"),
        "topSeedWins": series.get("topSeedWins"),
        "bottomSeed": series.get("bottomSeedTeamAbbrev"),
        "bottomSeedWins": series.get("bottomSeedWins"),
        # REG | OT | SO
        "periodType": period.get("periodType"),
        "periods": period.get("number"),
        "venue": (game.get("venue") or {}).get("default"),
        "away": side(away, a_score, h_score),
        "home": side(home, h_score, a_score),
        "sourceName": SOURCE_NAME,
        "sourceDatasetRef": f"{source_url}#game={game.get('id')}",
    }


def is_final(game):
    # OFF and FINAL both mean concluded in this API.
    return (game.get("status") or "").upper() in ("OFF", "FINAL")


# ------------------------------------------------------------------- fetch

def fetch_week(start, cache_dir=None, franchises=None):
    """One request returns seven days, scores included."""
    franchises = franchises if franchises is not None else load_franchises(cache_dir)
    url = f"{API}/schedule/{start}"
    payload = _get(url)

    games = []
    for day in payload.get("gameWeek") or []:
        for g in day.get("games") or []:
            games.append(normalize(g, franchises, url, day_date=day.get("date")))
    return games, payload.get("nextStartDate")


def fetch_range(start, end, cache_dir=None, progress=None):
    """
    Walk weeks from start to end.

    Follows the API's own nextStartDate where offered, falling back to a
    seven-day step so a missing pointer cannot stall the walk.
    """
    franchises = load_franchises(cache_dir)
    cur = start
    seen = set()
    out = []

    while cur and cur <= end:
        if cur in seen:
            break
        seen.add(cur)
        try:
            games, nxt = fetch_week(cur, cache_dir, franchises)
        except SourceError as e:
            if progress:
                progress(f"  {cur}: {e}")
            nxt = None
            games = []

        out.extend(games)
        if progress and games:
            progress(f"  week of {cur}: {len(games)} games")

        if not nxt or nxt <= cur:
            d = _date.fromisoformat(cur) + timedelta(days=7)
            nxt = d.isoformat()
        cur = nxt

    return out


def fetch_season(year, cache_dir=None, progress=None):
    """
    A hockey season straddles the new year, so `year` means the season that
    starts in that calendar year: 1993 covers 1993-09-01 to 1994-07-01.
    """
    return fetch_range(f"{year}-09-01", f"{year + 1}-07-01", cache_dir, progress)
