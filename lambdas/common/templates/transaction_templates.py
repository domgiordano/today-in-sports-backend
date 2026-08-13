"""
Questions built from transactions rather than games.

Same contract as the other template modules: every prompt is assembled from
fields on the event, and no model is asked what happened. The difference is
only in register - these ask who moved where and for how much, which is the
half of the sport that box scores do not record.

Distractors matter more here than anywhere else. "Which team did X join" is
trivial if the wrong answers are drawn from a different era or a different
league, so teams contemporary with the deal are used wherever the context
provides them.
"""

import hashlib

from lambdas.common.templates.mlb_templates import (
    pretty_date,
    tier_for,
    validate,
)

__all__ = ["generate", "validate"]


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


def _other_teams(ctx, exclude, when, n=3):
    """
    Teams that existed at the time of the deal, excluding the real answer.

    Era matters: offering the Arizona Diamondbacks as a wrong answer to a 1919
    question gives the answer away to anyone who knows the franchise did not
    exist. `ctx["teamsByEra"]` holds the clubs active in each decade.
    """
    pool = ctx.get("teamsByEra", {}).get(when // 10) or ctx.get("allTeams") or []
    out = [t for t in pool if t and t not in exclude]
    # Deterministic, so a regenerated corpus produces identical questions.
    out.sort(key=lambda t: hashlib.sha1(f"{when}{t}".encode()).hexdigest())
    return out[:n]


# ------------------------------------------------------------------ movement

def mc_destination(event, ctx):
    """Which club did the player join."""
    f = event["facts"]
    if not (f.get("toTeam") and f.get("player")):
        return []

    distractors = _other_teams(ctx, {f["toTeam"], f.get("fromTeam")}, event["year"])
    if len(distractors) < 3:
        return []

    if f.get("fromTeam"):
        prompt = (f"On this day in {event['year']}, the {f['fromTeam']} sent "
                  f"{f['player']} to which team?")
    else:
        prompt = (f"On this day in {event['year']}, {f['player']} signed with "
                  f"which team?")

    return [_q(event, "mc", prompt, f["toTeam"], distractors=distractors)]


def mc_origin(event, ctx):
    """Which club gave the player up. Only asked when both ends are known."""
    f = event["facts"]
    if not (f.get("fromTeam") and f.get("toTeam") and f.get("player")):
        return []

    distractors = _other_teams(ctx, {f["toTeam"], f["fromTeam"]}, event["year"])
    if len(distractors) < 3:
        return []

    prompt = (f"{pretty_date(event['gameDate'])}: the {f['toTeam']} acquired "
              f"{f['player']} from which club?")
    return [_q(event, "mc", prompt, f["fromTeam"], distractors=distractors)]


def mc_who_moved(event, ctx):
    """
    Which player was in the deal.

    Distractors are other genuinely notable players, so the question tests
    whether you know the deal rather than which name sounds familiar.
    """
    f = event["facts"]
    pool = ctx.get("starNames") or []
    if not (f.get("player") and f.get("toTeam") and f.get("fromTeam")):
        return []

    others = [n for n in pool if n not in (f.get("allPlayers") or [])]
    others.sort(key=lambda n: hashlib.sha1(f"{event['gameId']}{n}".encode()).hexdigest())
    if len(others) < 3:
        return []

    prompt = (f"On this day in {event['year']}, the {f['fromTeam']} traded "
              f"which player to the {f['toTeam']}?")
    return [_q(event, "mc", prompt, f["player"], distractors=others[:3])]


# --------------------------------------------------------------------- money

def numeric_sale_price(event, ctx):
    """How much changed hands. Only where the archive records an amount."""
    f = event["facts"]
    amount = f.get("amount")
    if not amount or not f.get("player"):
        return []

    prompt = (f"On this day in {event['year']}, {f['player']} was sold"
              + (f" to the {f['toTeam']}" if f.get("toTeam") else "")
              + ". How much cash was involved, in dollars?")

    return [_q(event, "numeric", prompt, amount,
               numericAnswer=amount,
               # Sale prices span orders of magnitude, so a flat tolerance is
               # meaningless. A quarter of the price keeps "roughly right"
               # rewarding at both $1,500 and $400,000.
               tolerance=max(1, round(amount * 0.25)))]


# ---------------------------------------------------------------- blockbuster

def numeric_deal_size(event, ctx):
    """How many players were involved in a multi-player deal."""
    f = event["facts"]
    count = f.get("playerCount") or 0
    if count < 4 or not f.get("fromTeam") or not f.get("toTeam"):
        return []

    prompt = (f"On this day in {event['year']}, the {f['fromTeam']} and "
              f"{f['toTeam']} completed a deal headlined by {f['player']}. "
              f"How many players did it involve?")

    return [_q(event, "numeric", prompt, count,
               numericAnswer=count, tolerance=1)]


TEMPLATES = {
    "star_trade": [mc_destination, mc_origin, mc_who_moved],
    "star_purchase": [mc_destination, numeric_sale_price],
    "star_free_agent": [mc_destination],
    "star_drafted": [mc_destination],
    "landmark_sale": [numeric_sale_price, mc_destination],
    "blockbuster_trade": [numeric_deal_size, mc_who_moved, mc_destination],
}


def build_context(events):
    """
    Distractor pools, derived from the events themselves.

    Teams are bucketed by decade so a wrong answer is always a club that
    existed at the time, and star names come from the deals rather than a list
    anyone had to write.
    """
    teams_by_era = {}
    all_teams = set()
    stars = set()

    for e in events:
        f = e.get("facts") or {}
        era = e["year"] // 10
        bucket = teams_by_era.setdefault(era, set())
        for key in ("fromTeam", "toTeam"):
            if f.get(key):
                bucket.add(f[key])
                all_teams.add(f[key])
        if f.get("player"):
            stars.add(f["player"])

    return {
        "teamsByEra": {k: sorted(v) for k, v in teams_by_era.items()},
        "allTeams": sorted(all_teams),
        "starNames": sorted(stars),
    }


def generate(events, ctx=None):
    ctx = ctx or build_context(events)
    out = []
    for event in events:
        for template in TEMPLATES.get(event.get("reason"), []):
            for q in template(event, ctx):
                if not validate(q):
                    out.append(q)
    return out
