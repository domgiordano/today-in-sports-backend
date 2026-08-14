"""
Lineup questions: eight names, four of whom started.

The unreduced version - reconstruct the batting order - is not a game, it is a
punishment. This is the version people can actually play: half the names in
front of you were there, half were not, and you pick.

Retrosheet game logs record *starters*, which is the right list to ask about:
it is a decision somebody made before the game rather than an accident of how
the game unfolded.

Decoys come from other notable games in the same era. Drawing them from any
year at all would make the question solvable by anyone who knows when a player
was active, which tests the wrong thing entirely.
"""

import hashlib

from lambdas.common.templates.mlb_templates import pretty_date, tier_for

__all__ = ["generate", "validate", "build_context"]

# Four real names and four decoys. Eight fits on a phone; twelve does not, and
# the extra difficulty is in the reading rather than the recall.
REAL_NAMES = 4
DECOY_NAMES = 4

# A game with fewer starters recorded than this has a partial lineup, and
# picking four from a partial list risks calling a real starter a decoy.
MIN_RECORDED = 12


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


def _pick(names, seed, count):
    """Deterministic selection, so a regenerated corpus is identical."""
    ordered = sorted(
        names, key=lambda n: hashlib.sha1(f"{seed}{n}".encode()).hexdigest())
    return ordered[:count]


def _q(event, qtype, prompt, answer, **kw):
    q = {
        "questionId": _qid(event["gameId"], qtype, prompt, answer),
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


def who_started(event, ctx):
    """Pick the four who started this game from a list of eight."""
    lineup = [n for n in (event.get("lineups") or []) if n]
    if len(lineup) < MIN_RECORDED:
        return []

    real = _pick(lineup, event["gameId"], REAL_NAMES)
    if len(real) < REAL_NAMES:
        return []

    # A decoy who actually played in this game would be marked wrong for a
    # correct answer, which is the one mistake this format must not make.
    played = {n.lower() for n in lineup}
    era_pool = (ctx or {}).get("namesByEra", {}).get(event["year"] // 10) or []
    candidates = [n for n in era_pool if n.lower() not in played]

    decoys = _pick(candidates, f"d{event['gameId']}", DECOY_NAMES)
    if len(decoys) < DECOY_NAMES:
        return []

    options = sorted(
        real + decoys,
        key=lambda n: hashlib.sha1(f"o{event['gameId']}{n}".encode()).hexdigest())

    prompt = (f"{pretty_date(event['gameDate'])}: four of these eight players "
              f"started this game. Which four?")

    return [_q(event, "multi", prompt, sorted(real),
               options=options,
               # Stated on the card, because "pick four" and "pick any" are
               # different games and guessing all eight should not win.
               chooseCount=REAL_NAMES)]


TEMPLATES = ["no_hitter", "perfect_game", "world_series_game7",
             "world_series_game", "postseason_shutout"]


def validate(q):
    problems = []
    if not q.get("sourceDatasetRef"):
        problems.append("missing sourceDatasetRef")
    if not q.get("sourceName"):
        problems.append("missing sourceName")
    if not (1 <= q.get("tier", 0) <= 5):
        problems.append("bad tier")

    if q["type"] == "multi":
        answer = q.get("answer") or []
        options = q.get("options") or []
        if len(answer) != REAL_NAMES:
            problems.append(f"{len(answer)} correct names, expected {REAL_NAMES}")
        if len(options) != REAL_NAMES + DECOY_NAMES:
            problems.append(f"{len(options)} options, "
                            f"expected {REAL_NAMES + DECOY_NAMES}")
        if not set(answer) <= set(options):
            problems.append("a correct name is missing from the options")
        if len(set(options)) != len(options):
            problems.append("duplicate options")
        if q.get("chooseCount") != len(answer):
            problems.append("chooseCount does not match the answer")

    return problems


def build_context(events):
    """
    Decoy pool, bucketed by decade.

    Built from the lineups in the corpus rather than a list anyone had to
    write, so it grows with the data and never names somebody who did not play.
    """
    names_by_era = {}
    for e in events:
        era = e["year"] // 10
        for name in e.get("lineups") or []:
            if name:
                names_by_era.setdefault(era, set()).add(name)
    return {"namesByEra": {k: sorted(v) for k, v in names_by_era.items()}}


def generate(events, ctx=None):
    ctx = ctx or build_context(events)
    out = []
    for event in events:
        if event.get("reason") not in TEMPLATES:
            continue
        for q in who_started(event, ctx):
            if not validate(q):
                out.append(q)
    return out
