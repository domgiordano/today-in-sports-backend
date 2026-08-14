"""
Pruning superseded questions.

The rule this file exists to protect: a question the generator no longer
produces is not a question anybody decided about — it is a leftover. Pruning
only drafts left 5,593 unanswerable clue ladders sitting approved and
quiz-eligible long after the rewrite that made them impossible to generate.

The exemptions matter as much as the rule, and each is here for a different
reason, so each gets its own test.
"""

import importlib.util
import pathlib


def _load():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "load_corpus.py"
    spec = importlib.util.spec_from_file_location("load_corpus", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decide(item, fresh_ids, rebuilt_sports, status):
    """
    The prune's own predicate, stated once.

    Mirrors the loop in load_corpus rather than reaching into it, because the
    loop is wrapped in DynamoDB pagination that a unit test has no business
    standing up.
    """
    if item["questionId"] in fresh_ids:
        return "keep"
    if item.get("sport") not in rebuilt_sports:
        return "keep"
    if item.get("authoredBy"):
        return "keep"
    if status == "used":
        return "warn"
    return "delete"


FRESH = {"still-generated"}
SPORTS = {"mlb"}


def test_a_superseded_approved_question_is_pruned():
    # The whole point. An approval describes a question that no longer exists.
    assert decide({"questionId": "gone", "sport": "mlb"},
                  FRESH, SPORTS, "approved") == "delete"


def test_a_superseded_draft_is_pruned():
    assert decide({"questionId": "gone", "sport": "mlb"},
                  FRESH, SPORTS, "draft") == "delete"


def test_a_still_generated_question_is_kept():
    assert decide({"questionId": "still-generated", "sport": "mlb"},
                  FRESH, SPORTS, "approved") == "keep"


def test_a_shipped_question_is_warned_about_not_deleted():
    # A published quiz points at it; deleting the row would leave that quiz
    # unresolvable.
    assert decide({"questionId": "gone", "sport": "mlb"},
                  FRESH, SPORTS, "used") == "warn"


def test_a_hand_authored_question_is_never_pruned():
    # Written from a cited sentence. No generator produces it, so "not
    # regenerated" says nothing at all about whether it is still good.
    assert decide({"questionId": "gone", "sport": "news",
                   "authoredBy": "dom@example.com"},
                  FRESH, {"news"}, "approved") == "keep"


def test_a_sport_this_run_did_not_rebuild_is_untouched():
    # The corpus file is often one sport. An unscoped prune would delete every
    # other sport's inventory on the grounds that this run did not produce it.
    assert decide({"questionId": "gone", "sport": "nhl"},
                  FRESH, SPORTS, "approved") == "keep"


def test_the_predicate_here_matches_the_one_in_the_script():
    """
    The test above restates the prune's logic, which is only safe while the
    script still reads the same way. This pins the exemptions by name so a
    change to either side shows up here rather than in production.
    """
    source = (pathlib.Path(__file__).resolve().parents[1]
              / "scripts" / "load_corpus.py").read_text()
    prune = source[source.index("# Prune superseded questions."):]
    for guard in ('if item["questionId"] in fresh_ids:',
                  'if item.get("sport") not in rebuilt_sports:',
                  'if item.get("authoredBy"):',
                  'if status == "used":'):
        assert guard in prune, f"prune no longer guards on: {guard}"
    assert 'for status in ("draft", "approved", "used"):' in prune
    _load()  # and it still imports


# ------------------------------------------------------------------ identity

def test_two_players_from_one_game_get_different_question_ids():
    """
    Every clue ladder shares one prompt — "Who is this?" — so hashing only
    (gameId, type, prompt) made the id a function of the game alone. Two
    players who debuted in the same game collided and one silently overwrote
    the other on write. 61 questions vanished that way, and because the
    survivor depended on generation order, an approved id could later come to
    mean a different player.
    """
    from lambdas.common.templates import ordering_templates as tpl

    def event(player, wins):
        return {
            "gameId": "19720922LAN0", "gameDate": "1972-09-22",
            "mmdd": "09-22", "year": 1972, "sport": "mlb",
            "league": "NL", "reason": "pitcher_win_milestone",
            "title": f"{player} reached {wins}",
            "facts": {"player": player, "careerWins": wins,
                      "team": "Los Angeles Dodgers"},
            "sourceName": "Retrosheet", "sourceDatasetRef": "r",
        }

    a = tpl.clue_ladder(event("Don Sutton", 150))
    b = tpl.clue_ladder(event("Davey Lopes", 200))
    assert a and b
    assert a[0]["questionId"] != b[0]["questionId"]


def test_the_same_question_still_gets_the_same_id():
    # Identity has to stay stable, or every reload orphans the whole bank.
    from lambdas.common.templates import ordering_templates as tpl
    event = {
        "gameId": "g1", "gameDate": "1972-09-22", "mmdd": "09-22",
        "year": 1972, "sport": "mlb", "league": "NL",
        "reason": "pitcher_win_milestone", "title": "t",
        "facts": {"player": "Don Sutton", "careerWins": 150,
                  "team": "Los Angeles Dodgers"},
        "sourceName": "Retrosheet", "sourceDatasetRef": "r",
    }
    assert (tpl.clue_ladder(event)[0]["questionId"]
            == tpl.clue_ladder(event)[0]["questionId"])


def test_an_answer_of_any_shape_hashes_stably():
    # Answers are strings, lists (ordering, multi) and dicts (map coordinates).
    from lambdas.common.templates import map_templates as tpl
    assert tpl._part({"lng": 2.0, "lat": 1.0}) == tpl._part({"lat": 1.0, "lng": 2.0})
    assert tpl._part(["b", "a"]) != tpl._part(["a", "b"])  # order is meaningful
    assert tpl._part("x") == "x"
