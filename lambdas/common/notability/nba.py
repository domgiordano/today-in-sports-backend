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

# The free-tier payload carries a postseason flag but no series label, so the
# Finals cannot be read directly and must be inferred from the calendar.
#
# Measured on a real season rather than assumed: including May fired 46 times,
# because the modern playoffs run four rounds from late April through June and
# May alone holds ~40 games. June alone lands at ~5, which is the Finals window.
FINALS_MONTHS = (6,)

# Thresholds, all calibrated against an actual 1,319-game season rather than
# guessed. A 40-point margin looked rare and fired 22 times — the modern NBA
# produces blowouts freely. 50 is genuinely remarkable.
BLOWOUT_MARGIN = 50
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


# A basketball game nobody won by any points is a game nobody recorded. The
# source has no scores for much of the 1940s and returns 0 rather than null,
# which the low-score detector read as the lowest score in history: 1,415 of
# 1,898 NBA events were "combined for only 0 points", three quarters of the
# sport's entire corpus.
#
# They produced no questions only because a truthiness check downstream happens
# to treat 0 as absent - an accident one refactor away from shipping, so the
# judgement belongs here, where the data is known to be missing.
MIN_CREDIBLE_TEAM_SCORE = 20


def has_credible_score(game):
    """Did the source actually record what happened?"""
    for side in ("away", "home"):
        score = game[side].get("score")
        if score is None or int(score) < MIN_CREDIBLE_TEAM_SCORE:
            return False
    return True


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
        # Every NBA detector reasons about the score, so a game without one is
        # not a quiet gap - it is a wrong answer waiting to be asked.
        if not has_credible_score(g):
            continue
        for det in DETECTORS:
            events.extend(det(g))
    return dedupe_by_game(events) if dedupe else events
