"""
Question templates — deterministic generation from detected events.

No model is asked what happened. Every prompt is assembled from fields that
came out of a real dataset row, and every answer is a value from that row.

Distractors are drawn from *the same date's real games* rather than invented.
Fake distractors are detectable by elimination; contemporaneous real ones are
not, which is what makes the multiple-choice type actually hard.
"""

import hashlib

CURRENT_YEAR = 2026

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def tier_for(year):
    """Recency ladder: 1 = last year, 5 = 30+ years ago."""
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


# ---------------------------------------------------------------- numeric

def _variant(event, options):
    """Deterministic phrasing choice so wording varies but stays stable per game."""
    return options[int(str(event["gameId"])[-1]) % len(options)]


def numeric_marathon_innings(event, ctx):
    if event["reason"] != "extra_innings_marathon":
        return []
    f = event["facts"]
    when = pretty_date(event["gameDate"])
    prompt = _variant(event, [
        f"On {when}, the {f['awayTeam']} and {f['homeTeam']} went well past "
        f"regulation. How many innings did the game last?",
        f"The {f['awayTeam']} and {f['homeTeam']} needed extra innings on {when}. "
        f"How many innings in total?",
        f"How long did the {f['awayTeam']}-{f['homeTeam']} marathon on {when} run, "
        f"in innings?",
    ])
    return [_q(event, "numeric", prompt, f["innings"],
               numericAnswer=f["innings"], tolerance=2)]


def numeric_postseason_shutout(event, ctx):
    if event["reason"] != "postseason_shutout":
        return []
    f = event["facts"]
    return [_q(event, "numeric",
               f"In {f['gameRef']} on "
               f"{pretty_date(event['gameDate'])}, the {f['winningTeam']} shut out the "
               f"{f['losingTeam']}. How many runs did the {f['winningTeam']} score?",
               f["winningRuns"], numericAnswer=f["winningRuns"], tolerance=1)]


def numeric_slugfest(event, ctx):
    if event["reason"] != "slugfest":
        return []
    f = event["facts"]
    return [_q(event, "numeric",
               f"On {pretty_date(event['gameDate'])}, the {f['awayTeam']} and "
               f"{f['homeTeam']} produced the highest-scoring game of the day. "
               f"How many runs did the two teams score combined?",
               f["combinedRuns"], numericAnswer=f["combinedRuns"], tolerance=4)]


def mc_postseason_winner(event, ctx):
    """Which team won a given postseason game — distractors from that day/era."""
    if event["reason"] not in ("postseason_extra_innings", "postseason_shutout",
                               "postseason_one_run"):
        return []
    f = event["facts"]
    pool = [t for t in ctx["teams_that_day"]
            if t not in (f["winningTeam"], f["losingTeam"])]
    if len(pool) < 2:
        return []
    return [_q(event, "mc",
               f"{f['gameRef']} on "
               f"{pretty_date(event['gameDate'])} finished "
               f"{f['winningRuns']}-{f['losingRuns']}. Who won it?",
               f["winningTeam"],
               distractors=[f["losingTeam"]] + pool[:2])]


def mc_combined_no_hitter(event, ctx):
    """A combined no-hitter is its own question, and a better one."""
    f = event["facts"]
    if event["reason"] != "no_hitter" or not f.get("combined"):
        return []
    pool = [t for t in ctx["teams_that_day"]
            if t not in (f["throwingTeam"], f["noHitTeam"])]
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"On {pretty_date(event['gameDate'])}, "
               f"{f.get('pitchersUsed', 'multiple')} pitchers combined on a no-hitter "
               f"against the {f['noHitTeam']}. Which team's staff did it?",
               f["throwingTeam"], distractors=pool[:3])]


def numeric_blowout_runs(event, ctx):
    if event["reason"] != "blowout":
        return []
    f = event["facts"]
    return [_q(event, "numeric",
               f"On {pretty_date(event['gameDate'])}, the {f['scoringTeam']} put up a "
               f"huge number against the {f['opponent']}. How many runs did they score?",
               f["runs"], numericAnswer=f["runs"], tolerance=3)]


def numeric_ws_margin(event, ctx):
    if event["reason"] not in ("world_series_game7", "world_series_game"):
        return []
    f = event["facts"]
    if f.get("winningRuns") is None:
        return []
    return [_q(event, "numeric",
               f"In World Series Game {f['gameNumber']} on {pretty_date(event['gameDate'])}, "
               f"the {f['winningTeam']} beat the {f['losingTeam']}. "
               f"How many runs did the {f['winningTeam']} score?",
               f["winningRuns"], numericAnswer=f["winningRuns"], tolerance=1)]


# ---------------------------------------------------------------- multiple choice

def mc_no_hitter_pitcher(event, ctx):
    """Distractors = winning pitchers from other games the same day."""
    if event["reason"] not in ("no_hitter", "perfect_game"):
        return []
    f = event["facts"]
    pitcher = f.get("pitcher")
    if not pitcher or f.get("attributionConfidence") != "high":
        return []

    pool = [p for p in ctx["pitchers_that_day"] if p and p != pitcher]
    if len(pool) < 3:
        return []
    distractors = pool[:3]

    kind = "perfect game" if event["reason"] == "perfect_game" else "no-hitter"
    return [_q(event, "mc",
               f"Who threw a {kind} on {pretty_date(event['gameDate'])}?",
               pitcher, distractors=distractors)]


def mc_no_hit_team(event, ctx):
    """Distractors = other teams that actually played that day."""
    if event["reason"] not in ("no_hitter", "perfect_game"):
        return []
    f = event["facts"]
    victim = f["noHitTeam"]
    pool = [t for t in ctx["teams_that_day"] if t not in (victim, f["throwingTeam"])]
    if len(pool) < 3:
        return []

    kind = "a perfect game" if event["reason"] == "perfect_game" else "a no-hitter"
    who = f.get("creditedTo") or f.get("pitcher") or f"the {f['throwingTeam']} pitching staff"
    verb = "combined on" if f.get("combined") else "threw"
    # `creditedTo` may start with a lowercase article ("the Orioles pitching staff")
    who = who[0].upper() + who[1:] if who else who
    return [_q(event, "mc",
               f"{who} {verb} {kind} on {pretty_date(event['gameDate'])}. "
               f"Which team was held hitless?",
               victim, distractors=pool[:3])]


def numeric_one_nothing_innings(event, ctx):
    if event["reason"] != "one_nothing_extras":
        return []
    f = event["facts"]
    return [_q(event, "numeric",
               f"On {pretty_date(event['gameDate'])}, the {f['winningTeam']} beat the "
               f"{f['losingTeam']} 1-0 — but it took extra innings. "
               f"How many innings did the game go?",
               f["innings"], numericAnswer=f["innings"], tolerance=1)]


def numeric_blowout_margin(event, ctx):
    """A second angle on blowouts so one event yields more than one question."""
    if event["reason"] != "blowout":
        return []
    f = event["facts"]
    if f.get("opponentRuns") is None:
        return []
    margin = f["runs"] - f["opponentRuns"]
    if margin < 10:
        return []
    return [_q(event, "numeric",
               f"On {pretty_date(event['gameDate'])}, the {f['scoringTeam']} routed the "
               f"{f['opponent']} {f['runs']}-{f['opponentRuns']}. "
               f"What was the margin of victory?",
               margin, numericAnswer=margin, tolerance=2)]


TEMPLATES = [
    numeric_marathon_innings,
    numeric_one_nothing_innings,
    numeric_blowout_margin,
    numeric_blowout_runs,
    numeric_ws_margin,
    numeric_postseason_shutout,
    numeric_slugfest,
    mc_no_hitter_pitcher,
    mc_no_hit_team,
    mc_combined_no_hitter,
    mc_postseason_winner,
]


def build_context(games):
    """Same-day real values used as distractor pools."""
    pitchers, teams = [], []
    for g in games:
        w = (g.get("decisions") or {}).get("winner") or {}
        if w.get("fullName"):
            pitchers.append(w["fullName"])
        for side in ("away", "home"):
            if g[side].get("team"):
                teams.append(g[side]["team"])
    return {
        "pitchers_that_day": list(dict.fromkeys(pitchers)),
        "teams_that_day": list(dict.fromkeys(teams)),
    }


def generate(events, games):
    ctx = build_context(games)
    out = []
    for ev in events:
        for tpl in TEMPLATES:
            out.extend(tpl(ev, ctx))
    return out


def validate(q):
    """A question missing provenance is invalid, full stop."""
    problems = []
    # Belt-and-braces: a null that reached the prompt is a factual defect, not a
    # cosmetic one, and must never survive to review.
    if "None" in q.get("prompt", ""):
        problems.append("null interpolated into prompt")
    if not q.get("sourceDatasetRef"):
        problems.append("missing sourceDatasetRef")
    if not q.get("sourceName"):
        problems.append("missing sourceName")
    if q.get("answer") in (None, ""):
        problems.append("missing answer")
    if q["type"] == "mc":
        d = q.get("distractors") or []
        if len(d) < 3:
            problems.append(f"only {len(d)} distractors")
        if q["answer"] in d:
            problems.append("answer duplicated in distractors")
        if len(set(d)) != len(d):
            problems.append("duplicate distractors")
    if q["type"] == "numeric" and q.get("numericAnswer") is None:
        problems.append("missing numericAnswer")
    if not (1 <= q.get("tier", 0) <= 5):
        problems.append("bad tier")
    return problems
