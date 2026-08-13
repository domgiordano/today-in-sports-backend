"""
NFL notability detectors.

The NFL's value to this corpus is seasonal as much as anything: its playoffs run
through January and the Super Bowl sits in early February, which is precisely
where baseball, and largely motorsport, contribute nothing.

Calibration is stricter here than in other sports because there are only ~285
games a season and the postseason is tiny — 13 games. A detector firing on most
playoff games would flood those dates.
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


DETECTORS = [
    detect_super_bowl,
    detect_conference_championship,
    detect_playoff_overtime,
    detect_playoff_blowout,
    detect_shootout,
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
