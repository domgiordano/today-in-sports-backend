"""
Ballpark map questions.

Two rules carry this format, and both are tested here rather than trusted:
only closed parks are asked about, and the prompt never names the clubs.
"""

from datetime import date

from lambdas.common.sources import parks as parks_source
from lambdas.common.templates import map_templates as tpl


def park(**overrides):
    base = {
        "parkId": "NYC15",
        "name": "Ebbets Field",
        "city": "Brooklyn",
        "state": "NY",
        "lat": 40.6643,
        "lng": -73.9385,
        "closed": "1957-09-24",
    }
    base.update(overrides)
    return base


def event(**overrides):
    base = {
        "gameId": "BRO193806150",
        "gameDate": "1938-06-15",
        "mmdd": "06-15",
        "year": 1938,
        "sport": "mlb",
        "league": "National League",
        "reason": "no_hitter",
        "park": "NYC15",
        "facts": {"pitcher": "Johnny Vander Meer",
                  "noHitTeam": "Brooklyn Dodgers",
                  "throwingTeam": "Cincinnati Reds"},
        "sourceName": "Retrosheet",
        "sourceDatasetRef": "https://www.retrosheet.org/gamelogs/gl1938.zip#x",
    }
    base.update(overrides)
    return base


def ctx(**overrides):
    parks = {"NYC15": park()}
    parks.update(overrides.pop("parks", {}))
    return tpl.build_context({}, parks)


# ------------------------------------------------------------- which parks

def test_a_closed_park_is_defunct():
    assert parks_source.is_defunct({"end": date(1957, 9, 24)})


def test_an_open_park_is_not():
    # A blank END in parkcode.txt means still in use. Fenway is answerable
    # from where the Red Sox play today, so it is not a history question.
    assert not parks_source.is_defunct({"end": None})


def test_a_park_closing_in_the_future_is_not_defunct():
    assert not parks_source.is_defunct({"end": date(TODAY_YEAR + 5, 1, 1)})


TODAY_YEAR = date.today().year


def test_only_defunct_geocoded_parks_reach_the_index():
    parks = {
        "NYC15": {"parkId": "NYC15", "name": "Ebbets Field", "aka": [],
                  "city": "Brooklyn", "state": "NY", "end": date(1957, 9, 24)},
        "BOS07": {"parkId": "BOS07", "name": "Fenway Park", "aka": [],
                  "city": "Boston", "state": "MA", "end": None},
        "STL05": {"parkId": "STL05", "name": "Robison Field", "aka": [],
                  "city": "St. Louis", "state": "MO", "end": date(1920, 6, 6)},
    }
    coords = {"Brooklyn, NY, USA": {"lat": 40.66, "lng": -73.94}}
    index = parks_source.build_index(parks, coords)

    # Fenway is open; Robison Field is closed but has no coordinate. Neither
    # produces a question, and the second is the important case — a missing
    # geocode must drop the park rather than pin it somewhere.
    assert set(index) == {"NYC15"}


def test_city_key_resolves_retrosheets_own_country_codes():
    # Retrosheet's STATE column is not two-letter codes throughout. Guessing
    # produced "Toronto, ONT, USA" - a place that does not exist - and
    # silently dropped every park outside the United States.
    assert parks_source._city_key({"city": "Toronto", "state": "ONT"}) == \
        "Toronto, Canada"
    assert parks_source._city_key({"city": "Tokyo", "state": "JAP"}) == \
        "Tokyo, Japan"
    assert parks_source._city_key({"city": "Sydney", "state": "Australia"}) == \
        "Sydney, Australia"
    assert parks_source._city_key({"city": "London", "state": "England"}) == \
        "London, United Kingdom"


def test_city_key_keeps_the_state_for_american_parks():
    # There is a Kansas City in two states and a Columbus in several, so the
    # state has to survive for domestic lookups.
    assert parks_source._city_key({"city": "Boston", "state": "MA"}) == \
        "Boston, MA, USA"


# ------------------------------------------------------------ the question

def test_a_park_question_names_the_person_not_the_clubs():
    [q] = tpl.map_park(event(), ctx())
    prompt = q["prompt"]
    assert "Vander Meer" in prompt
    # Naming either club would hand over the city to anyone who knows which
    # was at home, which is the whole thing this format is trying to ask.
    assert "Dodgers" not in prompt
    assert "Reds" not in prompt
    assert "Brooklyn" not in prompt


def test_the_answer_is_the_park_coordinate():
    [q] = tpl.map_park(event(), ctx())
    assert q["answer"] == {"lat": 40.6643, "lng": -73.9385}
    assert q["venueName"] == "Ebbets Field"
    assert q["venuePlace"] == "Brooklyn, NY"


def test_an_event_at_an_open_park_produces_nothing():
    # Open parks never enter the index, so the template simply finds no park.
    assert tpl.map_park(event(park="BOS07"), ctx()) == []


def test_an_event_with_no_named_person_produces_nothing():
    # A combined no-hitter credits a staff. "The Reds pitching staff threw a
    # no-hitter - where?" would have to name a club to make sense.
    e = event(facts={"pitcher": None, "combined": True})
    assert tpl.map_park(e, ctx()) == []


def test_a_game_after_the_park_closed_is_dropped():
    # A park code that outlives its own END date is a data error, and pinning
    # a map at it would be a confident wrong answer.
    e = event(gameDate="1960-06-15", year=1960)
    assert tpl.map_park(e, ctx()) == []


def test_milestone_wins_read_as_an_ordinal():
    e = event(reason="pitcher_win_milestone",
              facts={"player": "Warren Spahn", "careerWins": 300,
                     "team": "Milwaukee Braves"})
    [q] = tpl.map_park(e, ctx())
    assert "300th win" in q["prompt"]
    assert "Braves" not in q["prompt"]


def test_debut_and_finale_both_read():
    for reason, phrase in (("player_debut", "first game"),
                           ("player_finale", "last game")):
        e = event(reason=reason, facts={"player": "Gil Hodges"})
        [q] = tpl.map_park(e, ctx())
        assert phrase in q["prompt"]


def test_park_questions_pass_the_map_validator():
    [q] = tpl.map_park(event(), ctx())
    assert tpl.validate(q) == []


def test_a_park_pinned_at_null_island_is_rejected():
    # What a missing coordinate looks like after a float conversion, and it is
    # in the Gulf of Guinea rather than anywhere a game was played.
    [q] = tpl.map_park(event(), ctx(parks={"NYC15": park(lat=0.0, lng=0.0)}))
    assert "coordinate is null island" in tpl.validate(q)


def test_generate_routes_park_events_without_circuits():
    out = tpl.generate([event()], ctx())
    assert len(out) == 1
    assert out[0]["type"] == "map"


def test_no_parks_loaded_means_no_park_questions():
    assert tpl.generate([event()], tpl.build_context({}, {})) == []


# ------------------------------------------------- the park survives the trip

def test_the_corpus_builder_keeps_the_park_when_it_slims_a_game():
    """
    `_slim` exists to keep 155 seasons in memory, and it drops everything an
    event does not need. Leaving the park out cost every milestone its map
    question silently — the event was built from the slimmed game, so the code
    was simply gone by then, with nothing to notice it.
    """
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_corpus.py"
    spec = importlib.util.spec_from_file_location("build_corpus", path)
    build_corpus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_corpus)

    game = {
        "gameId": "BRO193806150",
        "gameDate": "1938-06-15",
        "park": "NYC15",
        "away": {"team": "Cincinnati Reds", "league": "NL", "leagueId": "NL"},
        "home": {"team": "Brooklyn Dodgers"},
        "sourceName": "Retrosheet",
        "sourceDatasetRef": "https://www.retrosheet.org/x",
    }

    assert build_corpus._slim(game)["park"] == "NYC15"

    event = build_corpus._player_event(
        game, "player_debut", 84, "a debut", {"player": "Someone"})
    assert event["park"] == "NYC15"


def test_every_map_template_key_is_a_reason_some_detector_emits():
    """
    A template keyed on a reason nobody produces makes no questions and no
    noise. Two of these were invented rather than read — `first_win` and
    `pole_to_win` against an F1 detector that emits `first_career_win` and
    `win_from_the_back` — so two of its five reasons silently generated
    nothing for as long as the file existed.
    """
    import re
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    sources = list((repo / "lambdas" / "common" / "notability").glob("*.py"))
    # build_corpus.py emits milestone reasons through its own event builders
    # rather than the notability package's, and they are just as real.
    sources.append(repo / "scripts" / "build_corpus.py")

    # Every quoted snake_case literal in the detector sources, rather than a
    # regex per call shape. Reasons are passed positionally, as dict values and
    # as reassignments, across line breaks — chasing each shape makes the test
    # brittle in a way that fails on refactors rather than on real bugs. This
    # is deliberately weaker: it proves the key appears somewhere a detector
    # could emit it, which is exactly what an invented key like `pole_to_win`
    # fails.
    emitted = set()
    for path in sources:
        emitted.update(re.findall(r'"([a-z][a-z_]{3,})"', path.read_text()))

    unmatched = set(tpl.TEMPLATES) - emitted
    assert not unmatched, f"map templates keyed on reasons nobody emits: {unmatched}"
