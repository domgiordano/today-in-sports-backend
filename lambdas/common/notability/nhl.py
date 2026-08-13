"""
NHL notability detectors.

Same principle as baseball: notability is computed from a real record, never
judged by a model. Hockey's structure makes several of these cleaner than their
baseball equivalents — the API states the series round, the game number within
the series and the wins needed to take it, so a Cup clincher is arithmetic
rather than inference.

Calibration rule carried over from MLB: a detector firing more than roughly
10-15 times per season is measuring something that merely happens. That is why
regular-season shutouts are absent here (they occur well over a hundred times a
season) while playoff shutouts are present.
"""

from lambdas.common.sources.nhl import is_final

PLAYOFFS = 3


def _other(side):
    return "home" if side == "away" else "away"


def _base(game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    return {
        "sport": "nhl",
        "league": "NHL",
        "leagueId": "NHL",
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


def has_usable_teams(game):
    return bool(game["away"].get("team")) and bool(game["home"].get("team"))


def _sides(game):
    win = "away" if game["away"].get("isWinner") else "home"
    return game[win], game[_other(win)]


def _series_facts(game, w, l):
    return {
        "round": game.get("seriesDescription"),
        "seriesAbbrev": game.get("seriesAbbrev"),
        "gameNumber": game.get("seriesGameNumber"),
        "winningTeam": w["team"], "losingTeam": l["team"],
        "winningScore": w.get("score"), "losingScore": l.get("score"),
        "periodType": game.get("periodType"),
        "periods": game.get("periods"),
        "venue": game.get("venue"),
    }


def detect_cup_clincher(game):
    """
    The game that ended a Stanley Cup Final.

    Derived, not inferred: the winner's series wins equal the wins needed.
    """
    if game.get("gameType") != PLAYOFFS or not is_final(game):
        return []
    if game.get("seriesAbbrev") != "SCF":
        return []

    needed = game.get("neededToWin")
    if not needed:
        return []

    w, l = _sides(game)
    wins = (game.get("topSeedWins") if w["teamId"] == game.get("topSeed")
            else game.get("bottomSeedWins"))
    if wins != needed:
        return []

    f = _series_facts(game, w, l)
    f["clinchedIn"] = game.get("seriesGameNumber")
    ot = (game.get("periodType") or "REG") != "REG"
    return [_base(game, "stanley_cup_clincher", 98,
                  f"{w['team']} won the Stanley Cup, beating the {l['team']} "
                  f"{w.get('score')}-{l.get('score')}"
                  f"{' in overtime' if ot else ''}", f)]


def detect_playoff_game_seven(game):
    if game.get("gameType") != PLAYOFFS or not is_final(game):
        return []
    if game.get("seriesGameNumber") != 7:
        return []
    w, l = _sides(game)
    f = _series_facts(game, w, l)
    return [_base(game, "playoff_game_seven", 94,
                  f"{f['round']} Game 7: {w['team']} beat the {l['team']} "
                  f"{w.get('score')}-{l.get('score')}", f)]


def detect_playoff_overtime(game):
    """Playoff overtime — sudden death, so every one of them decided the game."""
    if game.get("gameType") != PLAYOFFS or not is_final(game):
        return []
    if (game.get("periodType") or "REG") == "REG":
        return []
    w, l = _sides(game)
    f = _series_facts(game, w, l)
    periods = game.get("periods") or 4
    extra = periods - 3
    f["overtimePeriods"] = max(extra, 1)
    return [_base(game, "playoff_overtime", 86,
                  f"{f['round']} Game {f['gameNumber']}: {w['team']} won "
                  f"{w.get('score')}-{l.get('score')} in "
                  f"{'double ' if extra == 2 else 'triple ' if extra == 3 else ''}overtime",
                  f)]


def detect_playoff_shutout(game):
    if game.get("gameType") != PLAYOFFS or not is_final(game):
        return []
    w, l = _sides(game)
    if (l.get("score") or 0) != 0 or w.get("score") is None:
        return []
    f = _series_facts(game, w, l)
    return [_base(game, "playoff_shutout", 84,
                  f"{f['round']} Game {f['gameNumber']}: {w['team']} shut out "
                  f"the {l['team']} {w.get('score')}-0", f)]


def detect_goal_flood(game):
    """Combined 15+ goals. Rare enough to be genuinely remarkable."""
    if not is_final(game):
        return []
    a, h = game["away"].get("score"), game["home"].get("score")
    if a is None or h is None or (a + h) < 15:
        return []
    return [_base(game, "goal_flood", 80,
                  f"{game['away']['team']} and {game['home']['team']} combined "
                  f"for {a + h} goals",
                  {"awayTeam": game["away"]["team"], "homeTeam": game["home"]["team"],
                   "awayScore": a, "homeScore": h, "combined": a + h})]


DETECTORS = [
    detect_cup_clincher,
    detect_playoff_game_seven,
    detect_playoff_overtime,
    detect_playoff_shutout,
    detect_goal_flood,
]


def dedupe_by_game(events):
    """One event per game, highest notability wins — a Cup-clinching Game 7 in
    overtime legitimately trips three detectors."""
    best = {}
    for ev in events:
        key = ev["gameId"]
        if key not in best or ev["notabilityScore"] > best[key]["notabilityScore"]:
            best[key] = ev
    return sorted(best.values(), key=lambda e: (e["gameDate"], -e["notabilityScore"]))


def run(games, dedupe=True):
    events = []
    for g in games:
        if not has_usable_teams(g):
            continue
        for det in DETECTORS:
            events.extend(det(g))
    return dedupe_by_game(events) if dedupe else events
