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
import re
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

# One row per team identity the NHL has ever had, each with its own tricode.
#
# The franchise endpoint this used to read has one row per *franchise*, keyed on
# whichever abbreviation that franchise uses today - so franchise 15 is "DAL"
# and Minnesota's 2,235 games from 1967-1993 resolved to nothing, printing the
# bare code "MNS" as the team name. This endpoint has both: MNS is the
# Minnesota North Stars and DAL is the Dallas Stars, as separate rows.
#
# That the tricode is era-specific is the whole trick. The game log already
# carries the code that was correct on the day, so resolving it needs no season
# window and cannot pick the wrong era - MNS appears only in games from 1967 to
# 1993 and DAL only from 1993 on.
TEAMS_URL = "https://api.nhle.com/stats/rest/en/team"


def load_teams(cache_dir=None):
    """Every NHL team identity, keyed by the tricode used at the time."""
    cached = os.path.join(cache_dir, "nhl_teams.json") if cache_dir else None
    if cached and os.path.exists(cached):
        with open(cached) as f:
            data = json.load(f)
    else:
        data = _get(TEAMS_URL)
        if cached:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cached, "w") as f:
                json.dump(data, f)

    out = {}
    for team in data.get("data", []):
        code, name = team.get("triCode"), team.get("fullName")
        if not (code and name):
            continue
        # "Ottawa Senators (1917)" and "Winnipeg Jets (1979)" carry a
        # disambiguating year the API needs and a quiz prompt does not.
        out[code] = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
    return out


def team_name(teams, abbrev, season=None):
    """
    The club's name on the day, or None when the code is not an NHL club.

    None rather than the raw code, because the caller has to be able to tell a
    club apart from a national side. `season` is accepted and ignored: the
    tricode already encodes the era, and taking it keeps existing callers
    working.
    """
    return teams.get(abbrev)


def is_nhl_club(teams, abbrev):
    """
    Was this an NHL club at all?

    The schedule carries more than league games: All-Star squads (ALL, 1ST,
    2ND, PAC, CEN), national teams (CAN, SWE, URS, TCH) from the Canada Cup and
    Summit Series, and European clubs from exhibition tours. 482 of 65,766
    games have one, and a quiz that asks who won a Soviet Union versus Canada
    game and files it as an NHL result is wrong twice over.
    """
    return abbrev in teams


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
            "team": team_name(franchises, abbrev, season) or abbrev,
            "teamId": abbrev,
            "league": "NHL",
            "leagueId": "NHL",
            "score": score,
            "shots": t.get("sog"),
            "isWinner": (score is not None and opp_score is not None
                         and score > opp_score),
        }

    game_type = game.get("gameType")
    # An NHL fixture is one between two NHL clubs. All-Star squads, national
    # teams and touring European sides all appear on this schedule, and a
    # question calling a Canada-USSR result an NHL game is wrong twice over.
    is_league_game = (is_nhl_club(franchises, away.get("abbrev"))
                      and is_nhl_club(franchises, home.get("abbrev")))
    return {
        "sport": "nhl",
        "isLeagueGame": is_league_game,
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
    franchises = franchises if franchises is not None else load_teams(cache_dir)
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
    franchises = load_teams(cache_dir)
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
