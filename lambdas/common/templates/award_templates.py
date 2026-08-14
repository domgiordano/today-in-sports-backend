"""
Questions about award winners.

The announcement has a date, so these are date-anchored like everything else -
"on this day the MVP was announced" - rather than only attached to a season.

Distractors are other winners of the same award, which is what makes the
question about remembering a year rather than recognising a famous name: every
option is somebody who won it at some point.
"""

import hashlib

from lambdas.common.templates.mlb_templates import tier_for

__all__ = ["generate", "validate", "build_context"]


def _qid(*parts):
    """
    A question's identity.

    The answer is part of it, and has to be. Several templates build a constant
    prompt - every clue ladder reads "Who is this?" - so hashing only
    (gameId, type, prompt) made the id a function of the game alone. Two players
    who debuted in the same game collided, and one silently overwrote the other:
    61 questions vanished on write, and which copy survived depended on
    generation order, so an approved id could later come to mean a different
    player entirely.
    """
    return hashlib.sha1("|".join(_part(p) for p in parts).encode()).hexdigest()[:16]


def _part(value):
    """Stable text for anything an answer might be: a string, list or dict."""
    if isinstance(value, dict):
        return ",".join(f"{k}={_part(v)}" for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return ",".join(_part(v) for v in value)
    return str(value)


def _q(event, qtype, prompt, answer, **kw):
    q = {
        "questionId": _qid(event["gameId"], qtype, prompt, answer),
        "type": qtype,
        "tier": tier_for(event["year"]),
        "prompt": prompt,
        "answer": answer,
        "sport": event["sport"],
        "league": event["league"],
        "isNegroLeagues": False,
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


def _era_winners(ctx, award, year, span=2):
    """
    Winners of this award within a couple of decades either side.

    Widens rather than gives up when an era is thin - the first Cy Young was
    1956, so a 1957 question has very few contemporaries to draw on.
    """
    by_era = (ctx.get("winnersByAwardEra") or {}).get(award) or {}
    era = year // 10

    names = []
    for offset in range(span + 1):
        for candidate in ({era} if offset == 0 else {era - offset, era + offset}):
            names.extend(by_era.get(candidate) or [])
        if len(set(names)) >= 4:
            break

    return sorted(set(names)) or (ctx.get("winnersByAward", {}).get(award) or [])


def mc_who_won(event, ctx):
    """Who took this award in this season."""
    f = event["facts"]
    player, award = f.get("player"), f.get("award")
    if not (player and award):
        return []

    # The full name, not the abbreviation. "the NL MVP" trips the raw-team-code
    # guard - which exists to catch things like "the CL4" and cannot tell a
    # league abbreviation from a Retrosheet id - and the long form reads better
    # in a prompt anyway.
    label = f.get("awardFull") or award

    # Other winners of the same award, from the same era. Drawing from the
    # general population would make the question "which of these is a famous
    # baseball player" - and drawing from every year would offer Cal Ripken as
    # a wrong answer to a 1931 question, which anybody can eliminate on sight.
    pool = [n for n in _era_winners(ctx, award, event["year"]) if n != player]
    pool.sort(key=lambda n: hashlib.sha1(f"{event['gameId']}{n}".encode()).hexdigest())
    if len(pool) < 3:
        return []

    prompt = (f"On this day in {event['year']}, the {label} was announced. "
              f"Who won it for the {f.get('season', event['year'])} season?")
    return [_q(event, "mc", prompt, player, distractors=pool[:3])]


def numeric_award_year(event, ctx):
    """Which season the award was for. Only where it differs from the date."""
    f = event["facts"]
    season = f.get("season")
    if not season or not f.get("player"):
        return []

    label = f.get("awardFull") or f["award"]
    prompt = (f"{f['player']} won the {label}. For which season?")
    return [_q(event, "numeric", prompt, int(season),
               numericAnswer=int(season),
               # Two years either side still shows you knew the era.
               tolerance=2)]


TEMPLATES = {"award_winner": [mc_who_won, numeric_award_year]}


def validate(q):
    from lambdas.common.templates.mlb_templates import validate as base
    return base(q)


def build_context(events):
    """Winner pools per award, overall and bucketed by decade."""
    by_award = {}
    by_era = {}
    for e in events:
        f = e.get("facts") or {}
        award, player = f.get("award"), f.get("player")
        if not (award and player):
            continue
        by_award.setdefault(award, set()).add(player)
        by_era.setdefault(award, {}).setdefault(e["year"] // 10, set()).add(player)

    return {
        "winnersByAward": {k: sorted(v) for k, v in by_award.items()},
        "winnersByAwardEra": {
            award: {era: sorted(names) for era, names in eras.items()}
            for award, eras in by_era.items()
        },
    }


def generate(events, ctx=None):
    award_events = [e for e in events if e.get("reason") in TEMPLATES]
    ctx = ctx or build_context(award_events)
    out = []
    for event in award_events:
        for template in TEMPLATES[event["reason"]]:
            for q in template(event, ctx):
                if not validate(q):
                    out.append(q)
    return out
