"""
Question templates for hockey, motorsport and football.

Same contract as the baseball templates: every prompt is assembled from fields
that came out of a real dataset row, and every answer is a value from that row.
No model is asked what happened.

Distractor sourcing differs by sport and matters more than it looks. Baseball
draws from the same day's real games, which works because 15 games were played.
Hockey and football postseasons have far fewer games per day, so these draw from
other teams in the same event pool — still real, still contemporaneous, never
invented.
"""

import hashlib

CURRENT_YEAR = 2026

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def tier_for(year):
    age = CURRENT_YEAR - year
    if age <= 1:
        return 1
    if age <= 5:
        return 2
    if age <= 15:
        return 3
    if age <= 30:
        return 4
    return 5


def pretty_date(gd):
    y, m, d = gd.split("-")
    return f"{MONTHS[int(m)]} {int(d)}, {y}"


def _qid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _q(event, qtype, prompt, answer, **kw):
    q = {
        "questionId": _qid(event["gameId"], qtype, prompt),
        "type": qtype,
        "tier": tier_for(event["year"]),
        "prompt": prompt,
        "answer": answer,
        "sport": event["sport"],
        "league": event["league"],
        "isNegroLeagues": event.get("isNegroLeagues", False),
        "mmdd": event["mmdd"],
        "year": event["year"],
        "sourceEventId": event["gameId"],
        "sourceReason": event["reason"],
        "sourceName": event["sourceName"],
        "sourceDatasetRef": event["sourceDatasetRef"],
        "status": "draft",
    }
    q.update(kw)
    return q


def _pick(pool, exclude, n=3):
    """Deterministic, stable distractors drawn from a real pool."""
    seen, out = set(), []
    for item in pool:
        if not item or item in exclude or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) == n:
            break
    return out


# ------------------------------------------------------------------- hockey

def nhl_cup_winner(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "stanley_cup_clincher":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nhl_teams", []), {f["winningTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"Who won the Stanley Cup on {pretty_date(event['gameDate'])}?",
               f["winningTeam"], distractors=pool)]


def nhl_cup_series_length(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "stanley_cup_clincher":
        return []
    f = event["facts"]
    n = f.get("clinchedIn")
    if not n:
        return []
    return [_q(event, "numeric",
               f"The {f['winningTeam']} clinched the Stanley Cup on "
               f"{pretty_date(event['gameDate'])}. In which game of the Final "
               f"did they seal it?",
               n, numericAnswer=n, tolerance=0)]


def nhl_playoff_overtime(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "playoff_overtime":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nhl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"On {pretty_date(event['gameDate'])}, a {f['round']} game went to "
               f"overtime and finished {f['winningScore']}-{f['losingScore']}. "
               f"Who won it?",
               f["winningTeam"], distractors=pool)]


# --------------------------------------------------------------- motorsport

def f1_decider_winner(event, ctx):
    if event["sport"] != "f1" or event["reason"] != "championship_decider":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("f1_drivers", []), {f["winner"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"The {f['grandPrix']} on {pretty_date(event['gameDate'])} decided "
               f"the drivers' championship. Who won the race itself?",
               f["winner"], distractors=pool)]


def f1_first_win(event, ctx):
    if event["sport"] != "f1" or event["reason"] not in ("first_career_win", "debut_win"):
        return []
    f = event["facts"]
    pool = _pick(ctx.get("f1_drivers", []), {f["winner"]}, 3)
    if len(pool) < 3:
        return []
    debut = event["reason"] == "debut_win"
    return [_q(event, "mc",
               f"Who took his first Grand Prix victory at the {f['grandPrix']} on "
               f"{pretty_date(event['gameDate'])}"
               f"{', on his championship debut' if debut else ''}?",
               f["winner"], distractors=pool)]


def f1_grid_position(event, ctx):
    if event["sport"] != "f1" or event["reason"] != "win_from_the_back":
        return []
    f = event["facts"]
    grid = f.get("gridPosition")
    if grid is None:
        return []
    return [_q(event, "numeric",
               f"{f['winner']} won the {f['grandPrix']} on "
               f"{pretty_date(event['gameDate'])} after starting well down the "
               f"order. What grid position did he start from?",
               grid, numericAnswer=grid, tolerance=1)]


def f1_milestone(event, ctx):
    if event["sport"] != "f1" or event["reason"] != "milestone_win":
        return []
    f = event["facts"]
    n = f.get("careerWins")
    if not n:
        return []
    return [_q(event, "numeric",
               f"{f['winner']} reached a career milestone at the {f['grandPrix']} on "
               f"{pretty_date(event['gameDate'])}. How many Grand Prix wins did "
               f"that make?",
               n, numericAnswer=n, tolerance=0)]


# ----------------------------------------------------------------- football

def nfl_super_bowl_champion(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "super_bowl":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"Who won Super Bowl {f['superBowlNumber']}, played on "
               f"{pretty_date(event['gameDate'])}?",
               f["winningTeam"], distractors=pool)]


def nfl_super_bowl_score(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "super_bowl":
        return []
    f = event["facts"]
    return [_q(event, "numeric",
               f"In Super Bowl {f['superBowlNumber']}, the {f['winningTeam']} beat "
               f"the {f['losingTeam']}. How many points did the "
               f"{f['winningTeam']} score?",
               f["winningScore"], numericAnswer=f["winningScore"], tolerance=3)]


def nfl_playoff_overtime(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "playoff_overtime":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"A {f['round']} game on {pretty_date(event['gameDate'])} went to "
               f"overtime. Who won it?",
               f["winningTeam"], distractors=pool)]


TEMPLATES = [
    nhl_cup_winner, nhl_cup_series_length, nhl_playoff_overtime,
    f1_decider_winner, f1_first_win, f1_grid_position, f1_milestone,
    nfl_super_bowl_champion, nfl_super_bowl_score, nfl_playoff_overtime,
]


def build_context(events):
    """
    Real-name pools for distractors, gathered across the corpus.

    Cross-era pools are acceptable here and arguably better: asking which of
    four genuine NHL franchises won a Cup is a real question, whereas four teams
    that happened to play the same night is often a giveaway when only one
    postseason game was on.
    """
    nhl_teams, f1_drivers, nfl_teams = [], [], []
    for e in events:
        f = e.get("facts") or {}
        if e["sport"] == "nhl":
            nhl_teams += [f.get("winningTeam"), f.get("losingTeam")]
        elif e["sport"] == "f1":
            f1_drivers.append(f.get("winner"))
        elif e["sport"] == "nfl":
            nfl_teams += [f.get("winningTeam"), f.get("losingTeam")]

    def uniq(xs):
        return list(dict.fromkeys(x for x in xs if x))

    return {"nhl_teams": uniq(nhl_teams),
            "f1_drivers": uniq(f1_drivers),
            "nfl_teams": uniq(nfl_teams)}


def generate(events, ctx=None):
    ctx = ctx if ctx is not None else build_context(events)
    out = []
    for ev in events:
        for tpl in TEMPLATES:
            out.extend(tpl(ev, ctx))
    return out
