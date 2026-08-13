"""
Formula One notability detectors.

Motorsport suits this approach unusually well. Every Grand Prix has an exact
date, a grid and a classified result, and the f1db dump flags championship
deciders outright — so the most significant races in the sport need no
inference whatsoever.

Career-relative facts (first win, milestone win, debut win) depend on ordering
the whole history, so they are computed in the source loader and read here.

Calibration: roughly 15 Grands Prix a season historically (more lately), so a
detector firing on most races would be worthless. Each below is deliberately
rare.
"""


def _base(race, reason, score, title, facts):
    y, m, d = race["gameDate"].split("-")
    return {
        "sport": "f1",
        "league": "Formula One",
        "leagueId": "F1",
        "isNegroLeagues": False,
        "reason": reason,
        "notabilityScore": score,
        "gameId": race["gameId"],
        "gameDate": race["gameDate"],
        "year": int(y),
        "mmdd": f"{m}-{d}",
        "title": title,
        "facts": facts,
        "sourceName": race["sourceName"],
        "sourceDatasetRef": race["sourceDatasetRef"],
    }


def _facts(race):
    w = race.get("winner") or {}
    return {
        # Carried so a map question knows where the race was held. The source
        # has always emitted it; the facts simply dropped it on the floor.
        "circuitId": race.get("circuitId"),
        "grandPrix": race.get("grandPrix"),
        "officialName": race.get("officialName"),
        "round": race.get("round"),
        "winner": w.get("driver"),
        "constructor": w.get("constructor"),
        "gridPosition": w.get("gridPosition"),
        "fromPole": w.get("polePosition"),
        "careerWins": w.get("careerWins"),
        "careerStarts": w.get("careerStarts"),
        "podium": race.get("podium"),
        "laps": race.get("laps"),
    }


def has_winner(race):
    w = race.get("winner")
    return bool(w and w.get("driver"))


def detect_championship_decider(race):
    """The race that settled the drivers' title — flagged in the dump itself."""
    if not race.get("championshipDecider") or not has_winner(race):
        return []
    f = _facts(race)
    f["decidedDriversTitle"] = True
    return [_base(race, "championship_decider", 96,
                  f"The {race.get('grandPrix')} decided the drivers' championship; "
                  f"{f['winner']} won the race", f)]


def detect_debut_win(race):
    """
    A win on the driver's first World Championship start. Vanishingly rare
    outside 1950, when everyone's first start was the same race.
    """
    if not has_winner(race):
        return []
    w = race["winner"]
    if w.get("careerStarts") != 1 or w.get("careerWins") != 1:
        return []
    f = _facts(race)
    return [_base(race, "debut_win", 95,
                  f"{w['driver']} won the {race.get('grandPrix')} on his "
                  f"championship debut", f)]


def detect_first_career_win(race):
    """A driver's first Grand Prix victory."""
    if not has_winner(race):
        return []
    w = race["winner"]
    if w.get("careerWins") != 1 or w.get("careerStarts") == 1:
        return []  # a debut win is the rarer, separate claim
    f = _facts(race)
    return [_base(race, "first_career_win", 90,
                  f"{w['driver']} took his first Grand Prix win at the "
                  f"{race.get('grandPrix')}, in his "
                  f"{w.get('careerStarts')}th start", f)]


MILESTONES = {10, 25, 50, 75, 100}


def detect_milestone_win(race):
    if not has_winner(race):
        return []
    w = race["winner"]
    if w.get("careerWins") not in MILESTONES:
        return []
    f = _facts(race)
    return [_base(race, "milestone_win", 87,
                  f"{w['driver']} took his {w['careerWins']}th career win at the "
                  f"{race.get('grandPrix')}", f)]


def detect_win_from_the_back(race):
    """Victory from tenth on the grid or worse."""
    if not has_winner(race):
        return []
    grid = (race["winner"] or {}).get("gridPosition")
    if grid is None or grid < 10:
        return []
    f = _facts(race)
    return [_base(race, "win_from_the_back", 89,
                  f"{f['winner']} won the {race.get('grandPrix')} from "
                  f"P{grid} on the grid", f)]


DETECTORS = [
    detect_championship_decider,
    detect_debut_win,
    detect_first_career_win,
    detect_milestone_win,
    detect_win_from_the_back,
]


def dedupe_by_game(events):
    best = {}
    for ev in events:
        key = ev["gameId"]
        if key not in best or ev["notabilityScore"] > best[key]["notabilityScore"]:
            best[key] = ev
    return sorted(best.values(), key=lambda e: (e["gameDate"], -e["notabilityScore"]))


def run(races, dedupe=True):
    events = []
    for r in races:
        if not r.get("gameDate"):
            continue
        for det in DETECTORS:
            events.extend(det(r))
    return dedupe_by_game(events) if dedupe else events
