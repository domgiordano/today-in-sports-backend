"""
Map questions.

Coordinates come from the f1db dump and are never typed in or geocoded, so the
checks here are about what happens when one is missing rather than about
whether a particular circuit is in the right place.
"""

from lambdas.common.templates import map_templates as tpl

MONZA = {"circuitId": "monza", "name": "Autodromo Nazionale Monza",
         "place": "Monza", "country": "italy", "lat": 45.6206, "lng": 9.2894}

CTX = {"circuits": {"monza": MONZA}}


def _event(circuit_id="monza", reason="first_win", year=1978):
    return {
        "sport": "f1", "league": "Formula One", "reason": reason,
        "gameId": f"race-{year}", "gameDate": f"{year}-09-10",
        "year": year, "mmdd": "09-10", "title": "t",
        "facts": {"circuitId": circuit_id, "grandPrix": "Italian Grand Prix"},
        "sourceName": "f1db", "sourceDatasetRef": "https://github.com/f1db/f1db",
    }


def test_a_known_circuit_makes_a_map_question():
    qs = tpl.map_circuit(_event(), CTX)
    assert len(qs) == 1

    q = qs[0]
    assert q["type"] == "map"
    assert q["answer"] == {"lat": 45.6206, "lng": 9.2894}
    assert q["venueName"] == "Autodromo Nazionale Monza"
    assert "Italian Grand Prix" in q["prompt"]


def test_an_unknown_circuit_produces_nothing():
    """Better no question than a question pointing at the wrong place."""
    assert tpl.map_circuit(_event(circuit_id="nowhere"), CTX) == []
    assert tpl.map_circuit(_event(circuit_id=None), CTX) == []


def test_the_answer_coordinates_never_appear_in_the_prompt():
    q = tpl.map_circuit(_event(), CTX)[0]
    assert "45.6" not in q["prompt"]
    assert "9.28" not in q["prompt"]


def test_validation_rejects_a_coordinate_off_the_globe():
    q = tpl.map_circuit(_event(), CTX)[0]
    q["answer"] = {"lat": 200, "lng": 0}
    assert "coordinate is off the globe" in tpl.validate(q)


def test_validation_rejects_null_island():
    """
    What a missing coordinate looks like after a float conversion. It is in the
    Gulf of Guinea, and no circuit has ever been built there.
    """
    q = tpl.map_circuit(_event(), CTX)[0]
    q["answer"] = {"lat": 0, "lng": 0}
    assert "coordinate is null island" in tpl.validate(q)


def test_validation_requires_a_venue_name_to_reveal():
    q = tpl.map_circuit(_event(), CTX)[0]
    q.pop("venueName")
    assert "no venue name to reveal" in tpl.validate(q)


def test_generated_questions_validate():
    questions = tpl.generate([_event(reason=r) for r in tpl.TEMPLATES], CTX)
    assert questions
    for q in questions:
        assert tpl.validate(q) == []
