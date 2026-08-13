"""
Retrosheet source adapter — the production MLB source of record.

Chosen over the MLB Stats API for three reasons:

  * It is a downloadable file, not a live endpoint. The corpus is a one-time
    extraction of immutable history, so a local copy cannot be switched off.
  * Its licence explicitly permits commercial use (see ATTRIBUTION below).
    The MLB API returns an MLBAM copyright notice granting nothing.
  * It reaches 1871 rather than 1903.

It also removes a network round-trip: field 39/67 is "pitchers used", so
solo-versus-combined no-hitter attribution is answered by the row itself
instead of a follow-up boxscore request.

Field positions are taken from https://www.retrosheet.org/gamelogs/glfields.txt
and verified against known events in tests — see test_sources_retrosheet.py.

ATTRIBUTION (licence requirement — must appear prominently wherever this data
is used):

    The information used here was obtained free of charge from and is
    copyrighted by Retrosheet. Interested parties may contact Retrosheet at
    "www.retrosheet.org".
"""

import csv
import io
import math
import os
import time
import urllib.request
import zipfile
from datetime import date as _date

BASE = "https://www.retrosheet.org"
UA = "today-in-sports/0.1 (dominickj.giordano@gmail.com)"
SOURCE_NAME = "retrosheet"

ATTRIBUTION = (
    "The information used here was obtained free of charge from and is "
    'copyrighted by Retrosheet. Interested parties may contact Retrosheet at '
    '"www.retrosheet.org".'
)

# The regular-season log for a year does NOT contain postseason games — gl1991
# ends on 1991-10-06, two weeks before that World Series started. Ingesting only
# the season file silently drops every World Series, LCS and Division Series
# game, which are among the most notable events in the corpus.
POSTSEASON_FILES = {
    "World Series":              "glws.zip",
    "League Championship Series": "gllc.zip",
    "Division Series":           "gldv.zip",
    "Wild Card":                 "glwc.zip",
}

# 0-based indices into the 161-field game-log record.
F = {
    "date": 0, "gameNumber": 1, "dayOfWeek": 2,
    "vTeam": 3, "vLeague": 4, "hTeam": 6, "hLeague": 7,
    "vScore": 9, "hScore": 10,
    "outs": 11, "dayNight": 12, "park": 16, "attendance": 17,
    # visiting offence (fields 22-38)
    "vAB": 21, "vH": 22, "vHR": 25, "vHBP": 29, "vBB": 30, "vK": 32,
    # visiting pitching (39-43) — "1 means it was a complete game"
    "vPitchers": 38,
    # visiting defence (44-49)
    "vE": 45,
    # home offence (50-66)
    "hAB": 49, "hH": 50, "hHR": 53, "hHBP": 57, "hBB": 58, "hK": 60,
    "hPitchers": 66,
    "hE": 73,
    "wpId": 93, "wpName": 94, "lpId": 95, "lpName": 96,
    "svId": 97, "svName": 98,
    "vStartPitcherId": 101, "vStartPitcher": 102,
    "hStartPitcherId": 103, "hStartPitcher": 104,
    # Starting lineups: 9 players per side as (id, name, position) triples.
    "vLineupStart": 105,
    "hLineupStart": 132,
    "acquisition": 160,
}

LINEUP_SIZE = 9

LEAGUE_NAMES = {
    "AL": "American League",
    "NL": "National League",
    "AA": "American Association",
    "UA": "Union Association",
    "PL": "Players League",
    "FL": "Federal League",
    "NA": "National Association",
}


class SourceError(Exception):
    pass


def _fetch(path, cache_dir):
    """Download to cache_dir once; subsequent calls read the local copy."""
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, os.path.basename(path))
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local

    url = f"{BASE}/gamelogs/{path}" if not path.startswith("http") else path
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(local, "wb") as f:
            f.write(r.read())
    except Exception as e:
        raise SourceError(f"could not fetch {url}: {e}") from e
    time.sleep(0.5)  # be polite to a free volunteer-run archive
    return local


def _rows_from_zip(local_zip):
    with zipfile.ZipFile(local_zip) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        raw = z.read(name).decode("latin-1")
    return list(csv.reader(io.StringIO(raw)))


# ---------------------------------------------------------------- team names

def load_team_names(cache_dir):
    """
    Map a historical team code plus a date to the name in use at the time.

    Retrosheet game logs carry the *historical* code, and CurrentNames.csv gives
    each code a validity window. Honouring it is what yields "Brooklyn Robins"
    for a 1920 game rather than anachronistically calling them the Dodgers.
    """
    local = _fetch(f"{BASE}/CurrentNames.csv", cache_dir)
    entries = {}
    with open(local, newline="", encoding="latin-1") as f:
        for row in csv.reader(f):
            if len(row) < 9:
                continue
            hist, league, city, nick = row[1], row[2], row[4], row[5]
            start, end = row[7], row[8]
            entries.setdefault(hist, []).append({
                "league": league,
                "name": f"{city} {nick}".strip(),
                "start": _parse_mdy(start),
                "end": _parse_mdy(end),
            })
    return entries


def _parse_mdy(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        m, d, y = s.split("/")
        return _date(int(y), int(m), int(d))
    except Exception:
        return None


def team_name(lookup, code, game_date):
    """
    Resolve a code to the franchise name in use on that date.

    Returns None when the code cannot be resolved, rather than the code itself.
    CurrentNames.csv does not cover every club that ever played — the 1890
    Cleveland entry keyed `CL4` is absent — and falling back to the raw code
    puts it straight into a question: "the CL4 routed the Pittsburgh
    Alleghenys". A missing name is a game to skip, not a name to invent.
    """
    options = lookup.get(code)
    if not options:
        return None
    for o in options:
        start, end = o["start"], o["end"]
        if start and game_date < start:
            continue
        if end and game_date > end:
            continue
        return o["name"]
    return options[-1]["name"]


def looks_like_a_raw_code(name):
    """
    A short all-caps token is a Retrosheet id, not a team name.

    Belt and braces alongside `team_name` returning None: a lookup could also
    resolve to something equally useless, and either way it must not reach a
    prompt.
    """
    if not name:
        return True
    stripped = name.strip()
    return len(stripped) <= 4 and stripped.upper() == stripped


# ---------------------------------------------------------------- normalise

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def innings_from_outs(outs):
    """
    Length in outs -> innings.

    54 outs is a full nine. 51 means the home team led and never batted in the
    ninth, which is still a nine-inning game — so this rounds up rather than
    dividing. 58 -> 10 (the 1991 World Series Game 7), 71 -> 12.
    """
    o = _int(outs)
    if not o:
        return None
    return math.ceil(o / 6)


def _players(row, gdate):
    """
    Pull player identity out of a game-log record.

    Game logs give starters, not every appearance, so a career total derived
    from them is a career of *starts*. That is the right unit for pitcher wins
    (a win is already an award to one pitcher) and a good proxy for position
    players, but it is not a full games-played count and should not be labelled
    as one.
    """
    def person(id_key, name_key):
        pid, name = row[F[id_key]].strip(), row[F[name_key]].strip()
        if not pid or name in ("", "(none)"):
            return None
        return {"id": pid, "name": name}

    lineups = []
    for side, start in (("away", F["vLineupStart"]), ("home", F["hLineupStart"])):
        for i in range(LINEUP_SIZE):
            base = start + i * 3
            if base + 2 >= len(row):
                break
            pid, name, pos = row[base].strip(), row[base + 1].strip(), row[base + 2].strip()
            if not pid or name in ("", "(none)"):
                continue
            lineups.append({"id": pid, "name": name, "position": pos,
                            "side": side, "battingOrder": i + 1})

    return {
        "winningPitcher": person("wpId", "wpName"),
        "losingPitcher": person("lpId", "lpName"),
        "savingPitcher": person("svId", "svName"),
        "awayStarter": person("vStartPitcherId", "vStartPitcher"),
        "homeStarter": person("hStartPitcherId", "hStartPitcher"),
        "lineups": lineups,
    }


def normalize(row, names, series=None, game_number=None, games_in_series=None):
    """
    Convert one game-log record into the shared normalized game shape, so the
    existing detectors in notability/mlb.py run unchanged across both sources.
    """
    d = row[F["date"]]
    gdate = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    gd = _date(int(d[0:4]), int(d[4:6]), int(d[6:8]))

    v_runs, h_runs = _int(row[F["vScore"]]), _int(row[F["hScore"]])
    v_code, h_code = row[F["vTeam"]], row[F["hTeam"]]

    def side(prefix, code, league_code, runs, opp_runs):
        return {
            "team": team_name(names, code, gd),
            "teamId": code,
            "league": LEAGUE_NAMES.get(league_code, league_code),
            "leagueId": league_code,
            "runs": runs,
            "hits": _int(row[F[f"{prefix}H"]]),
            "atBats": _int(row[F[f"{prefix}AB"]]),
            "walks": _int(row[F[f"{prefix}BB"]]),
            "hitByPitch": _int(row[F[f"{prefix}HBP"]]),
            "errors": _int(row[F[f"{prefix}E"]]),
            # Straight from the record — no second request needed to tell a
            # solo no-hitter from a combined one.
            "pitchersUsed": _int(row[F[f"{prefix}Pitchers"]]),
            "isWinner": (runs is not None and opp_runs is not None and runs > opp_runs),
        }

    # Retrosheet's own game id convention: date + home team + game number.
    game_id = f"{d}{h_code}{row[F['gameNumber']]}"

    return {
        "sport": "mlb",
        "gameId": game_id,
        "gameDate": gdate,
        "gameType": "R" if series is None else "P",
        "season": int(d[0:4]),
        "status": "Final",
        "innings": innings_from_outs(row[F["outs"]]),
        "seriesDescription": series or "Regular Season",
        "seriesGameNumber": game_number,
        "gamesInSeries": games_in_series,
        "away": side("v", v_code, row[F["vLeague"]], v_runs, h_runs),
        "home": side("h", h_code, row[F["hLeague"]], h_runs, v_runs),
        "decisions": {"winner": {"fullName": row[F["wpName"]] or None},
                      "loser": {"fullName": row[F["lpName"]] or None}},
        # Player identity, needed for career milestones. Retrosheet ids are
        # stable across a career ("mcdoj001"), which is what makes "300th win"
        # computable with an exact date rather than only a season total.
        "players": _players(row, gdate),
        "park": row[F["park"]],
        "sourceName": SOURCE_NAME,
        "sourceDatasetRef": (
            f"{BASE}/gamelogs/{'gl%s.zip' % d[0:4] if series is None else POSTSEASON_FILES.get(series, '')}"
            f"#game={game_id}"
        ),
    }


# ---------------------------------------------------------------- public API

def fetch_season(year, cache_dir):
    """Regular-season games for a year, normalized."""
    names = load_team_names(cache_dir)
    rows = _rows_from_zip(_fetch(f"gl{year}.zip", cache_dir))
    return [normalize(r, names) for r in rows if len(r) >= 161]


def fetch_postseason(year, cache_dir):
    """
    Postseason games for a year, normalized, with series game numbers derived.

    The `gameNumber` field is the doubleheader indicator ("0", "1", "2"), not
    the series game number — so position within each series is computed by
    ordering that series' games by date.
    """
    names = load_team_names(cache_dir)
    out = []
    for series, filename in POSTSEASON_FILES.items():
        try:
            rows = _rows_from_zip(_fetch(filename, cache_dir))
        except SourceError:
            continue

        season_rows = [r for r in rows
                       if len(r) >= 161 and r[F["date"]].startswith(str(year))]
        if not season_rows:
            continue

        # Group by matchup so concurrent series (two LCS in the same year) are
        # numbered independently rather than interleaved.
        by_matchup = {}
        for r in season_rows:
            key = tuple(sorted([r[F["vTeam"]], r[F["hTeam"]]]))
            by_matchup.setdefault(key, []).append(r)

        for matchup_rows in by_matchup.values():
            matchup_rows.sort(key=lambda r: (r[F["date"]], r[F["gameNumber"]]))
            total = len(matchup_rows)
            for i, r in enumerate(matchup_rows, start=1):
                out.append(normalize(r, names, series=series,
                                     game_number=i, games_in_series=total))
    return out


def fetch_year(year, cache_dir):
    """Everything for a season — regular season plus postseason."""
    return fetch_season(year, cache_dir) + fetch_postseason(year, cache_dir)
