"""
NBA notability detectors.

Calibration is the whole problem in basketball. An 82-game, 30-team season is
about 1,230 games — the largest regular season of any sport here — so thresholds
that feel rare in hockey fire constantly. Every rule below is set against that,
and the numbers chosen are the ones a fan would recognise as remarkable rather
than merely unusual.

The NBA earns its place on the calendar rather than on volume: it plays October
to June, covering precisely the weeks that stay empty after baseball, hockey,
football and motorsport.
"""

from lambdas.common.sources.balldontlie import is_final

# An NBA Finals game is the only postseason game whose round is knowable from
# the free-tier payload, which carries a postseason flag but no series label.
# So Finals detection is inferred from date and is deliberately conservative --
# see detect_finals_era_game.
FINALS_MONTHS = (5, 6)

# Thresholds. A 40-point margin happens a handful of times a season; a 30-point
# playoff margin is rarer still.
BLOWOUT_MARGIN = 40
PLAYOFF_BLOWOUT_MARGIN = 30
HIGH_SCORING_COMBINED = 280
LOW_SCORING_COMBINED = 130


def _base(game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    return {
        "sport": "nba",
        "league": "NBA",
        "leagueId": "NBA",
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
    other = "home" if win == "away" else "away"
    return game[win], game[other]


def _facts(game, w, l):
    return {
        "round": game.get("seriesDescription"),
        "season": game.get("season"),
        "winningTeam": w["team"], "losingTeam": l["team"],
        "winningScore": w.get("score"), "losingScore": l.get("score"),
        "combinedPoints": game.get("combinedPoints"),
        "margin": game.get("margin"),
        "isPlayoff": game.get("isPlayoff"),
    }


def detect_finals_era_game(game):
    """
    A playoff game in late May or June.

    The free tier flags a game as postseason but does not name the round, so the
    Finals cannot be identified outright. June playoff basketball is almost
    always the Finals, and this is scored below a known championship rather than
    claiming to be one — the title says "NBA Finals era", not "won the title".
    """
    if not game.get("isPlayoff") or not is_final(game):
        return []
    month = int(game["gameDate"][5:7])
    if month not in FINALS_MONTHS:
        return []

    w, l = _sides(game)
    f = _facts(game, w, l)
    f["roundInferred"] = True
    return [_base(game, "nba_late_playoff", 88,
                  f"{w['team']} beat the {l['team']} {w.get('score')}-"
                  f"{l.get('score')} in the {game['gameDate'][:4]} playoffs", f)]


def detect_playoff_blowout(game):
    if not game.get("isPlayoff") or not is_final(game):
        return []
    if (game.get("margin") or 0) < PLAYOFF_BLOWOUT_MARGIN:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "nba_playoff_blowout", 84,
                  f"{w['team']} routed the {l['team']} {w.get('score')}-"
                  f"{l.get('score')} in the playoffs", f)]


def detect_blowout(game):
    if not is_final(game) or (game.get("margin") or 0) < BLOWOUT_MARGIN:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "nba_blowout", 78,
                  f"{w['team']} beat the {l['team']} by {game['margin']}, "
                  f"{w.get('score')}-{l.get('score')}", f)]


def detect_shootout(game):
    if not is_final(game):
        return []
    total = game.get("combinedPoints")
    if total is None or total < HIGH_SCORING_COMBINED:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "nba_shootout", 82,
                  f"{game['away']['team']} and {game['home']['team']} combined "
                  f"for {total} points", f)]


def detect_rock_fight(game):
    """
    A combined total under 130 — extraordinary in the modern game and a marker
    of the pre-shot-clock and slow-down eras, which makes it good deep-tier
    material.
    """
    if not is_final(game):
        return []
    total = game.get("combinedPoints")
    if total is None or total > LOW_SCORING_COMBINED:
        return []
    w, l = _sides(game)
    f = _facts(game, w, l)
    return [_base(game, "nba_low_score", 80,
                  f"{w['team']} and {l['team']} combined for only {total} points",
                  f)]


DETECTORS = [
    detect_finals_era_game,
    detect_playoff_blowout,
    detect_blowout,
    detect_shootout,
    detect_rock_fight,
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
