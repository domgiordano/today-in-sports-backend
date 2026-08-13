"""
The auto-approval gate.

Validation asks "is this well-formed". This asks the softer question "would a
person reading it think it was a good question", and the two are genuinely
different: a prompt can validate perfectly and still hand over its own answer.

Nothing here ever rejects. A flag means a person should look.
"""

import pytest

from scripts.auto_review import flags_for


def _q(**kw):
    base = {
        "type": "mc", "prompt": "On this day in 1971, who was traded to Detroit?",
        "answer": "Frank Robinson", "distractors": ["Hank Aaron", "Willie Mays",
                                                    "Bob Gibson"],
        "mmdd": "08-13", "tier": 4,
    }
    base.update(kw)
    return base


def test_a_good_question_is_approved():
    assert flags_for(_q()) == []


def test_a_prompt_containing_its_own_answer_is_held():
    q = _q(prompt="On this day in 1971, Frank Robinson was traded to whom?")
    assert "answer appears in the prompt" in flags_for(q)


def test_a_single_token_name_is_held():
    """
    Retrosheet records some nineteenth-century players by surname alone.
    Not wrong, but it reads as a data gap and a person should decide.
    """
    assert "answer is a single-token name" in flags_for(_q(answer="Keefe"))


def test_a_distractor_overlapping_the_answer_is_held():
    q = _q(answer="New York Yankees",
           distractors=["Yankees", "Boston Red Sox", "Chicago Cubs"])
    assert "a distractor overlaps the answer" in flags_for(q)


def test_a_very_short_prompt_is_held():
    assert "prompt is very short" in flags_for(_q(prompt="Who?"))


@pytest.mark.parametrize("value,expected", [
    (-5, "negative numeric answer"),
    (10 ** 9, "implausibly large numeric answer"),
    ("banana", "numeric answer is not a number"),
    (None, "no numeric answer"),
])
def test_implausible_numeric_answers_are_held(value, expected):
    q = _q(type="numeric", numericAnswer=value, answer=value,
           prompt="How many innings did that game go for in total?")
    assert expected in flags_for(q)


def test_a_sensible_numeric_answer_is_approved():
    q = _q(type="numeric", numericAnswer=15, answer=15, distractors=None,
           prompt="How many innings did that game go for in total?")
    assert flags_for(q) == []


def test_near_identical_ordering_items_are_held():
    q = _q(type="ordering",
           prompt="Put these four moments in the order they happened.",
           answer=["Chicago White Sox beat the Boston Red Sox",
                   "Chicago White Sox beat the Boston Red Sox again",
                   "Something else entirely happened here",
                   "A fourth distinct moment in time"],
           distractors=None)
    q["items"] = q["answer"]
    assert "two items read almost identically" in flags_for(q)


def test_a_clue_containing_the_answer_is_held():
    q = _q(type="clue", answer="Frank Robinson",
           prompt="Who is this? Take a clue at a time.",
           clues=["This happened in the 1970s.",
                  "Frank Robinson was involved."],
           distractors=None)
    assert "answer appears in a clue" in flags_for(q)


def test_negro_leagues_questions_are_always_held():
    """
    Factually sound, but the framing is a decision that should not be made
    silently by a script.
    """
    assert "Negro Leagues - check the framing" in flags_for(
        _q(isNegroLeagues=True))


def test_flags_accumulate_rather_than_stopping_at_the_first():
    q = _q(prompt="Who?", answer="Keefe")
    assert len(flags_for(q)) >= 2


class TestAnachronisticNames:
    """
    The MLB source resolves a club to the name it carried on the date. The
    basketball and hockey sources do not, so a 1956 game comes back as "Los
    Angeles Lakers routed the Atlanta Hawks" - both clubs in the wrong city,
    the Lakers being in Minneapolis until 1960 and the Hawks in St. Louis until
    1968. Confidently, checkably wrong, and found by reading a random sample of
    what was about to be approved.
    """

    def test_an_old_basketball_question_is_held(self):
        q = _q(sport="nba", year=1956,
               prompt="On March 19, 1956, the Los Angeles Lakers routed the "
                      "Atlanta Hawks 133-75. What was the margin?")
        assert "team name may be anachronistic - check the city" in flags_for(q)

    def test_an_old_hockey_question_is_held(self):
        assert "team name may be anachronistic - check the city" in flags_for(
            _q(sport="nhl", year=1960))

    def test_a_modern_basketball_question_is_approved(self):
        assert flags_for(_q(sport="nba", year=2019)) == []

    def test_baseball_is_exempt_because_it_resolves_historical_names(self):
        """CurrentNames.csv is what yields "Brooklyn Robins" for a 1920 game."""
        assert flags_for(_q(sport="mlb", year=1920)) == []


class TestReloadPreservesDecisions:
    """
    Regression, and an expensive one. The loader wrote status "draft"
    unconditionally, so the next reload silently reverted every approval -
    17,546 in a single run - while the pruning code twenty lines below
    carefully explained that a decision is not a reload's to discard.
    """

    def test_an_undecided_question_loads_as_a_draft(self):
        from scripts.load_corpus import status_for
        assert status_for("q1", {}) == "draft"

    @pytest.mark.parametrize("status", ["approved", "rejected", "used"])
    def test_an_existing_decision_survives_a_reload(self, status):
        from scripts.load_corpus import status_for
        assert status_for("q1", {"q1": status}) == status
