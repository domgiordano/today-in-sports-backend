"""
Soccer source adapter, reading openfootball/football.json.

Chosen over openfootball's primary text repos deliberately. Those store matches
in a hand-written DSL whose shape changed over time — a 1966 World Cup file puts
the date on the match line, a 2014 file uses a date header with indented
fixtures and goalscorer lines beneath. Parsing that across ninety years is a lot
of surface for silent errors, and this project has already produced enough of
those from far simpler data.

`football.json` is the same project's structured export: one JSON file per
league per season, with dates, teams and full-time scores. CC0-1.0, so it is
public domain and carries no attribution requirement.

**Coverage: 2010-11 onward**, ten European leagues. That serves tiers 1 to 3 and
cannot reach 4 or 5 — the same ceiling as nflverse. It earns its place on the
calendar rather than on depth: domestic seasons run August to May, so soccer
covers December, January and February, which is precisely where baseball,
motorsport and American football contribute little or nothing.
"""

import json
import os
import urllib.error
import urllib.request

RAW = "https://raw.githubusercontent.com/openfootball/football.json/master"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"
SOURCE_NAME = "football.json"

ATTRIBUTION = (
    "Football data from openfootball/football.json "
    "(https://github.com/openfootball/football.json), public domain (CC0-1.0)."
)

# league file -> display name. Second tiers are included because they double
# winter coverage at no extra cost.
LEAGUES = {
    "en.1": "English Premier League",
    "en.2": "English Championship",
    "de.1": "Bundesliga",
    "de.2": "2. Bundesliga",
    "es.1": "La Liga",
    "it.1": "Serie A",
    "fr.1": "Ligue 1",
    "nl.1": "Eredivisie",
    "pt.1": "Primeira Liga",
    "at.1": "Austrian Bundesliga",
}


class SourceError(Exception):
    pass


def _fetch(season, league, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, f"{season}_{league}.json")
    if os.path.exists(local) and os.path.getsize(local) > 0:
        with open(local) as f:
            return json.load(f)

    url = f"{RAW}/{season}/{league}.json"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Not every league exists in every season; that is expected.
            raise SourceError(f"no {league} for {season}") from e
        raise SourceError(f"HTTP {e.code} for {url}") from e
    except Exception as e:
        raise SourceError(f"{type(e).__name__} for {url}") from e

    with open(local, "w") as f:
        json.dump(data, f)
    return data


def _full_time(match):
    """Full-time score, or None if the match has no result."""
    score = match.get("score") or {}
    ft = score.get("ft")
    if not ft or len(ft) != 2:
        return None
    try:
        return int(ft[0]), int(ft[1])
    except (TypeError, ValueError):
        return None


def normalize(match, league_code, league_name, season):
    ft = _full_time(match)
    if not ft or not match.get("date"):
        return None
    home_goals, away_goals = ft
    home, away = match.get("team1"), match.get("team2")
    if not home or not away:
        return None

    def side(team, goals, opp):
        return {
            "team": team,
            "teamId": team,
            "league": league_name,
            "leagueId": league_code,
            "score": goals,
            "isWinner": goals > opp,
        }

    return {
        "sport": "soccer",
        "gameId": f"{league_code}-{season}-{match['date']}-{home}-{away}".replace(" ", "_"),
        "gameDate": match["date"],
        "season": season,
        "league": league_name,
        "leagueId": league_code,
        "round": match.get("round"),
        "status": "Final",
        "combinedGoals": home_goals + away_goals,
        "margin": abs(home_goals - away_goals),
        "isDraw": home_goals == away_goals,
        # team1 is home, team2 away, per the export's own convention.
        "home": side(home, home_goals, away_goals),
        "away": side(away, away_goals, home_goals),
        "sourceName": SOURCE_NAME,
        "sourceDatasetRef": f"{RAW}/{season}/{league_code}.json#{match['date']}",
    }


def fetch_league_season(season, league_code, cache_dir):
    """One league, one season. `season` is the export's form, e.g. '2023-24'."""
    data = _fetch(season, league_code, cache_dir)
    name = data.get("name") or LEAGUES.get(league_code, league_code)
    out = []
    for m in data.get("matches", []):
        g = normalize(m, league_code, name, season)
        if g:
            out.append(g)
    return out


def fetch_season(season, cache_dir, leagues=None):
    """Every available league for a season."""
    out = []
    for code in (leagues or LEAGUES):
        try:
            out.extend(fetch_league_season(season, code, cache_dir))
        except SourceError:
            continue
    return out


def available_seasons():
    """Season directory names, oldest first."""
    seasons = [f"{y}-{str(y + 1)[2:]}" for y in range(2010, 2025)]
    seasons.append("2025")
    return seasons
