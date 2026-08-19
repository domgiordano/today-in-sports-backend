"""
NFL notability detectors.

The NFL's value to this corpus is seasonal as much as anything: its playoffs run
through January and the Super Bowl sits in early February, which is precisely
where baseball, and largely motorsport, contribute nothing.

Calibration is stricter here than in other sports because there are only ~285
games a season and the postseason is tiny — 13 games. A detector firing on most
playoff games would flood those dates.

That strictness went too far. Four of the five original detectors required
`isPlayoff`, so 6,967 regular-season games across 27 seasons could produce an
event only by way of a 90-point shootout — thirteen games in the whole dataset.
The NFL contributed 126 events against baseball's 9,543, which is not a
statement about the sport but about what was being looked for. The
regular-season detectors below are calibrated on the real distribution, quoted
per season so the fire rate can be checked rather than guessed:

    shutout                 6.7/season
    blowout (35+)           6.0/season
    overtime               15.4/season
    shootout (80+)          2.8/season
    rock fight (13 or less) 2.0/season
    one-point game         10.9/season
"""


def _base(game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    return {
        "sport": "nfl",
        "league": "NFL",
        "leagueId": "NFL",
        "isNegroLeagues": False,
        "reason": reason,
        "notabilityScore": score,
        "gameId": game["gameId"],
        "gameDate": game["gameDate"],
        "year": int(y),
        "mmdd": f"{m}-{d}",
        "title": title,
        "facts": facts,
        "sourceName": game["sourceName"],
        "sourceDatasetRef": game["sourceDatasetRef"],
    }


def _sides(game):
    win = "away" if game["away"]["isWinner"] else "home"
    other = "home" if win == "away" else "away"
    return game[win], game[other]


def _facts(game, w, l):
    return {
        "round": game.get("seriesDescription"),
        "season": game.get("season"),
        "winningTeam": w["team"], "losingTeam": l["team"],
        "winningScore": w["score"], "losingScore": l["score"],
        "combinedPoints": game.get("combinedPoints"),
        "margin": game.get("margin"),
        "overtime": game.get("overtime"),
    }


def has_usable_teams(game):
    return bool(game["away"].get("team")) and bool(game["home"].get("team"))


def detect_super_bowl(game):
    if game.get("gameType") != "SB":
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    # Super Bowl numbering starts with the 1966 season; the dataset begins at
    # 1999, so this is derived rather than read.
    f["superBowlNumber"] = (game.get("season") or 0) - 1965
    ot = " in overtime" if game.get("overtime") else ""
    return [_base(game, "super_bowl", 97,
                  f"{w['team']} won Super Bowl {f['superBowlNumber']}, beating the "
                  f"{l['team']} {w['score']}-{l['score']}{ot}", f)]


def detect_conference_championship(game):
    if game.get("gameType") != "CON":
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "conference_championship", 88,
                  f"{w['team']} won the Conference Championship over the "
                  f"{l['team']} {w['score']}-{l['score']}", f)]


def detect_playoff_overtime(game):
    if not game.get("isPlayoff") or not game.get("overtime"):
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "playoff_overtime", 90,
                  f"{f['round']}: {w['team']} beat the {l['team']} "
                  f"{w['score']}-{l['score']} in overtime", f)]


def detect_playoff_blowout(game):
    """A postseason game decided by 28 or more."""
    if not game.get("isPlayoff") or (game.get("margin") or 0) < 28:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "playoff_blowout", 82,
                  f"{f['round']}: {w['team']} routed the {l['team']} "
                  f"{w['score']}-{l['score']}", f)]


def detect_shootout(game):
    """Combined 90+ points — rare in any era."""
    if (game.get("combinedPoints") or 0) < 90:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "shootout", 84,
                  f"{w['team']} and {l['team']} combined for "
                  f"{game['combinedPoints']} points", f)]


def detect_shutout(game):
    """
    A team held scoreless. 6.7 a season, and the rare NFL result that is
    remembered as a defensive performance rather than an offensive one.
    """
    if game.get("isPlayoff"):
        return []
    w, l = _sides(game)
    if l["score"] != 0:
        return []
    f = _facts(game, w, l)
    return [_base(game, "shutout", 86,
                  f"{w['team']} shut out the {l['team']} "
                  f"{w['score']}-0", f)]


def detect_regular_season_blowout(game):
    """
    Decided by 35 or more. Set above the playoff threshold of 28 on purpose:
    28 fires 19 times a season in the regular season and stops being notable.
    """
    if game.get("isPlayoff") or (game.get("margin") or 0) < 35:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "regular_season_blowout", 80,
                  f"{w['team']} routed the {l['team']} "
                  f"{w['score']}-{l['score']}", f)]


def detect_regular_season_overtime(game):
    """
    Scored below the other regular-season detectors: overtime is a real hook
    for a question but the least remarkable thing on this list, so when a game
    is both an overtime game and a shutout the dedupe keeps the shutout.
    """
    if game.get("isPlayoff") or not game.get("overtime"):
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "regular_season_overtime", 74,
                  f"{w['team']} beat the {l['team']} "
                  f"{w['score']}-{l['score']} in overtime", f)]


def detect_regular_season_shootout(game):
    """Combined 80+ outside the playoffs — 2.8 a season."""
    if game.get("isPlayoff") or (game.get("combinedPoints") or 0) < 80:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "regular_season_shootout", 85,
                  f"{w['team']} and {l['team']} combined for "
                  f"{game['combinedPoints']} points", f)]


def detect_rock_fight(game):
    """The opposite extreme: 13 points or fewer between them, 2.0 a season."""
    if game.get("isPlayoff") or (game.get("combinedPoints") or 99) > 13:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "rock_fight", 82,
                  f"{w['team']} beat the {l['team']} {w['score']}-{l['score']} "
                  f"in a game that produced {game['combinedPoints']} points", f)]


def detect_one_point_game(game):
    """Decided by a single point — the margin that turns on one kick."""
    if game.get("isPlayoff") or (game.get("margin") or 0) != 1:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "one_point_game", 78,
                  f"{w['team']} beat the {l['team']} by a point, "
                  f"{w['score']}-{l['score']}", f)]


DETECTORS = [
    detect_super_bowl,
    detect_conference_championship,
    detect_playoff_overtime,
    detect_playoff_blowout,
    detect_shootout,
    detect_shutout,
    detect_regular_season_blowout,
    detect_regular_season_overtime,
    detect_regular_season_shootout,
    detect_rock_fight,
    detect_one_point_game,
]


def dedupe_by_game(events):
    best = {}
    for ev in events:
        key = ev["gameId"]
        if key not in best or ev["notabilityScore"] > best[key]["notabilityScore"]:
            best[key] = ev
    return sorted(best.values(), key=lambda e: (e["gameDate"], -e["notabilityScore"]))


def run(games, dedupe=True):
    events = []
    for g in games:
        if not has_usable_teams(g) or not g.get("gameDate"):
            continue
        for det in DETECTORS:
            events.extend(det(g))
    return dedupe_by_game(events) if dedupe else events
