"""
MLB source adapter — fetch complete game data for a date from the MLB Stats API.

Free, no key, verified back to 1903. Returns normalized raw game rows; this
module makes no judgment about which games matter. That is the detectors' job.

Politeness: the API's rate limits are undocumented. Every call is throttled and
failures are retried with backoff rather than hammered.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://statsapi.mlb.com/api/v1"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"

THROTTLE_SECONDS = 0.4
MAX_RETRIES = 4

SOURCE_NAME = "mlb-stats-api"


class SourceError(Exception):
    pass


def _get(path, params):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(THROTTLE_SECONDS)
                return json.load(r), url
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


def fetch_date(date_str):
    """
    Return (games, source_url) for a single YYYY-MM-DD.

    Each game is a normalized dict. `raw` retains the API payload so detectors
    can reach fields we haven't normalized yet without a re-fetch.
    """
    payload, url = _get("schedule", {
        "sportId": 1,
        "date": date_str,
        "hydrate": "linescore,team,decisions",
    })

    dates = payload.get("dates") or []
    if not dates:
        return [], url

    games = []
    for g in dates[0].get("games", []):
        ls = g.get("linescore", {}) or {}
        lst = ls.get("teams", {}) or {}
        teams = g.get("teams", {}) or {}

        def side(key):
            box = lst.get(key, {}) or {}
            meta = teams.get(key, {}) or {}
            team = meta.get("team") or {}
            league = team.get("league") or {}
            return {
                "team": team.get("name"),
                "teamId": team.get("id"),
                "league": league.get("name"),
                "leagueId": league.get("id"),
                "runs": box.get("runs"),
                "hits": box.get("hits"),
                "errors": box.get("errors"),
                "isWinner": meta.get("isWinner"),
            }

        # Use the API's officialDate, never the queried date. A night game at
        # 23:05Z on Oct 10 has officialDate Oct 11, so the schedule endpoint
        # returns the same gamePk under two calendar dates. For a date-anchored
        # quiz that would both duplicate the game and file it under a day it was
        # not played. officialDate is the local game date — the one people mean
        # by "on this date".
        official = g.get("officialDate") or date_str

        games.append({
            "sport": "mlb",
            "gameId": g.get("gamePk"),
            "gameDate": official,
            "queriedDate": date_str,
            "gameType": g.get("gameType"),          # R, F, D, L, W (W = World Series)
            "season": g.get("season"),
            "status": (g.get("status") or {}).get("detailedState"),
            "innings": ls.get("currentInning"),
            "scheduledInnings": ls.get("scheduledInnings"),
            # 'Regular Season' | 'World Series' | 'Championship' | ... — the API's own
            # round label. Preferred over inferring from gameType, which has no code
            # for the Negro Leagues games now included under sportId=1.
            "seriesDescription": g.get("seriesDescription"),
            "away": side("away"),
            "home": side("home"),
            "decisions": g.get("decisions") or {},
            "seriesGameNumber": g.get("seriesGameNumber"),
            "gamesInSeries": g.get("gamesInSeries"),
            "sourceName": SOURCE_NAME,
            "sourceDatasetRef": f"{url}#gamePk={g.get('gamePk')}",
            "raw": g,
        })
    return games, url


def is_final(game):
    return (game.get("status") or "").startswith("Final")
