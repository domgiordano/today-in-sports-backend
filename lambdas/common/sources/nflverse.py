"""
NFL source adapter, reading nflverse release assets.

nflverse publishes bulk CSV/parquet as versioned GitHub release assets, which
makes it a file rather than an endpoint — the same durability property that made
Retrosheet and f1db the right choices for their sports.

**Coverage ceiling worth knowing: 1999 onward.** That serves tiers 1 to 3 and
cannot reach tiers 4 and 5. Pro-Football-Reference has the deeper history but
prohibits scraping and licenses through Sportradar, so older NFL needs a
different source rather than a workaround.

Assets used:
  * releases/download/schedules/games.csv  — every game, 1999+, with scores,
    game_type (REG/WC/DIV/CON/SB) and an overtime flag.
  * releases/download/teams/teams_colors_logos.csv — abbreviation to full name.
"""

import csv
import os
import urllib.request

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
GAMES_URL = f"{BASE}/schedules/games.csv"
TEAMS_URL = f"{BASE}/teams/teams_colors_logos.csv"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"

SOURCE_NAME = "nflverse"
ATTRIBUTION = "NFL data from nflverse (https://github.com/nflverse/nflverse-data)."

# The dataset's own round codes.
GAME_TYPE_LABEL = {
    "REG": "Regular Season",
    "WC": "Wild Card",
    "DIV": "Divisional Round",
    "CON": "Conference Championship",
    "SB": "Super Bowl",
}


class SourceError(Exception):
    pass


def _fetch(url, cache_dir, filename):
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, filename)
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(local, "wb") as f:
            f.write(r.read())
    except Exception as e:
        raise SourceError(f"could not fetch {url}: {e}") from e
    return local


def load_teams(cache_dir):
    path = _fetch(TEAMS_URL, cache_dir, "nfl_teams.csv")
    with open(path, newline="", encoding="utf-8") as f:
        return {row["team_abbr"]: row["team_name"]
                for row in csv.DictReader(f) if row.get("team_abbr")}


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def load_games(cache_dir):
    """Every completed game, normalized. Scheduled future games are dropped."""
    teams = load_teams(cache_dir)
    path = _fetch(GAMES_URL, cache_dir, "nfl_games.csv")

    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            date = (row.get("gameday") or "").strip()
            a_score, h_score = _int(row.get("away_score")), _int(row.get("home_score"))
            if not date or a_score is None or h_score is None:
                continue  # not played yet

            a_abbr, h_abbr = row.get("away_team"), row.get("home_team")
            gtype = row.get("game_type") or "REG"

            def side(abbr, score, opp):
                return {
                    "team": teams.get(abbr, abbr),
                    "teamId": abbr,
                    "league": "NFL",
                    "leagueId": "NFL",
                    "score": score,
                    "isWinner": score > opp,
                }

            out.append({
                "sport": "nfl",
                "gameId": row.get("game_id"),
                "gameDate": date,
                "season": _int(row.get("season")),
                "week": _int(row.get("week")),
                "gameType": gtype,
                "seriesDescription": GAME_TYPE_LABEL.get(gtype, gtype),
                "isPlayoff": gtype != "REG",
                "overtime": (row.get("overtime") or "0").strip() in ("1", "TRUE", "True"),
                "status": "Final",
                "combinedPoints": a_score + h_score,
                "margin": abs(a_score - h_score),
                "away": side(a_abbr, a_score, h_score),
                "home": side(h_abbr, h_score, a_score),
                "sourceName": SOURCE_NAME,
                "sourceDatasetRef": f"{GAMES_URL}#game_id={row.get('game_id')}",
            })
    return out
