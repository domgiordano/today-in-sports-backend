"""
Map questions: where did this happen.

The only format here that asks something spatial, and the one people are most
likely to describe to somebody else. It reuses the closest-guess mechanic the
numeric questions already use - credit falls away with distance - so a player
who knows the rough part of the world still scores.

Formula One first, because f1db ships latitude and longitude for every circuit
it knows. Nothing is geocoded and no coordinate is typed in by hand: the same
dump that supplies the race supplies the location, which is the same standard
every other question in this project is held to.

Ballparks came second, and needed two rules rather than one.

The first is which park is worth asking about, and it is `is_defunct` over in
`sources/parks.py`: an open park is answerable from where the club plays today,
which makes it a question about the present rather than about the day it is
anchored to. A closed one - Ebbets Field, Sportsman's Park, the Polo Grounds -
cannot be derived from anything current.

The second is how the question is worded, and it is the more important of the
two. Naming the clubs hands over the answer, because whoever was at home tells
you the city. So a park question names *the person and what they did* and
never who was playing whom. That restricts it to events the corpus knows a
name for, which is the right trade: "Vander Meer threw his second straight
no-hitter - where?" is a real question, and its answer is Brooklyn.
"""

import hashlib

from lambdas.common.templates.mlb_templates import pretty_date, tier_for

__all__ = ["generate", "validate", "build_context"]


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


def map_circuit(event, ctx):
    """Where in the world was this Grand Prix run."""
    facts = event.get("facts") or {}
    circuits = (ctx or {}).get("circuits") or {}

    circuit = circuits.get(facts.get("circuitId"))
    if not circuit:
        return []

    gp = facts.get("grandPrix")
    if not gp:
        return []

    prompt = (f"{pretty_date(event['gameDate'])}: the {gp} was run on this "
              f"day. Tap the map where you think the circuit is.")

    return [_q(event, "map", prompt,
               {"lat": circuit["lat"], "lng": circuit["lng"]},
               # Revealed after answering, so the result can name the place
               # rather than only showing a pin.
               venueName=circuit["name"],
               venuePlace=circuit.get("place"),
               venueCountry=circuit.get("country"))]


# How each achievement reads when the clubs cannot be mentioned. Every phrase
# is built from a name the corpus holds plus a number it counted, so nothing
# here asserts anything the event does not already say.
def _achievement(event, facts):
    reason = event.get("reason")
    person = facts.get("pitcher") or facts.get("player")
    if not person:
        return None

    if reason == "perfect_game":
        return f"{person} threw a perfect game"
    if reason == "no_hitter":
        # Combined no-hitters credit a staff rather than a person, and
        # `pitcher` is set to None for them upstream — so this is only ever
        # reached with a single named pitcher.
        return f"{person} threw a no-hitter"
    if reason == "pitcher_win_milestone":
        wins = facts.get("careerWins")
        return (f"{person} recorded the {_ordinal(wins)} win of his career"
                if wins else None)
    if reason == "player_debut":
        return f"{person} played the first game of his career"
    if reason == "player_finale":
        return f"{person} played the last game of his career"
    return None


def _ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def map_park(event, ctx):
    """
    Where was this game played — for parks that no longer exist.

    The prompt deliberately omits both clubs. Naming them would let anyone who
    knows which was at home derive the city, turning a history question into a
    lookup, and the whole point of restricting this to closed parks is that the
    answer cannot be derived from anything a player already knows about today.
    """
    parks = (ctx or {}).get("parks") or {}
    park = parks.get(event.get("park"))
    if not park:
        return []

    facts = event.get("facts") or {}
    achievement = _achievement(event, facts)
    if not achievement:
        return []

    # A park that closed before the event is a data error, not a question.
    if park.get("closed") and park["closed"] < event["gameDate"]:
        return []

    prompt = (f"{pretty_date(event['gameDate'])}: {achievement}. Tap the map "
              f"where the game was played.")

    return [_q(event, "map", prompt,
               {"lat": park["lat"], "lng": park["lng"]},
               venueName=park["name"],
               venuePlace=f"{park['city']}, {park['state']}",
               venueCountry=None)]


# The keys are the reason codes the detectors actually emit. Two of these were
# invented rather than read - `first_win` and `pole_to_win` match nothing the
# F1 detector produces, which are `first_career_win` and `win_from_the_back` -
# so two of its five reasons silently made no map questions at all. A wrong key
# here costs inventory without ever failing.
TEMPLATES = {
    "championship_decider": [map_circuit],
    "debut_win": [map_circuit],
    "first_career_win": [map_circuit],
    "milestone_win": [map_circuit],
    "win_from_the_back": [map_circuit],
    "no_hitter": [map_park],
    "perfect_game": [map_park],
    "pitcher_win_milestone": [map_park],
    "player_debut": [map_park],
    "player_finale": [map_park],
}


def validate(q):
    problems = []
    if not q.get("sourceDatasetRef"):
        problems.append("missing sourceDatasetRef")
    if not q.get("sourceName"):
        problems.append("missing sourceName")
    if not (1 <= q.get("tier", 0) <= 5):
        problems.append("bad tier")

    if q["type"] == "map":
        answer = q.get("answer") or {}
        try:
            lat, lng = float(answer["lat"]), float(answer["lng"])
        except (TypeError, ValueError, KeyError):
            problems.append("map answer is not a coordinate")
        else:
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                problems.append("coordinate is off the globe")
            # Null Island is what a missing coordinate looks like once it has
            # been through a float conversion, and it is in the Gulf of Guinea.
            if lat == 0 and lng == 0:
                problems.append("coordinate is null island")
        if not q.get("venueName"):
            problems.append("no venue name to reveal")

    return problems


def build_context(circuits, parks=None):
    return {"circuits": circuits or {}, "parks": parks or {}}


def generate(events, ctx=None):
    ctx = ctx or {"circuits": {}, "parks": {}}
    out = []
    for event in events:
        for template in TEMPLATES.get(event.get("reason"), []):
            for q in template(event, ctx):
                if not validate(q):
                    out.append(q)
    return out
