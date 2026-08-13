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

Baseball parks are the obvious next source - Retrosheet's parkcode.txt gives a
city and state for every park ever used - but city-to-coordinate needs a
geocoding pass that does not exist yet, and inventing coordinates would be
exactly the kind of confident wrongness the rest of this codebase avoids.
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


TEMPLATES = {
    "championship_decider": [map_circuit],
    "first_win": [map_circuit],
    "milestone_win": [map_circuit],
    "pole_to_win": [map_circuit],
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


def build_context(circuits):
    return {"circuits": circuits or {}}


def generate(events, ctx=None):
    ctx = ctx or {"circuits": {}}
    out = []
    for event in events:
        for template in TEMPLATES.get(event.get("reason"), []):
            for q in template(event, ctx):
                if not validate(q):
                    out.append(q)
    return out
