"""
Narrative candidates: validation, and the citation that must survive.

The interesting tests here are not the happy path. They are the ones that
prove a caller cannot produce a question asserting something its source does
not say, because that is the only property keeping this source usable.
"""

import pytest

from lambdas.common import narrative_dynamo as nd


def candidate(**overrides):
    base = {
        "mmdd": "12-06",
        "yearEventId": "2008#news-tebow-named-starter",
        "gameId": "news-tebow-named-starter",
        "gameDate": "2008-12-06",
        "year": 2008,
        "sport": "news",
        "league": "Sport",
        "reason": "narrative_event",
        "title": "Rovers name teenager as first-choice keeper",
        "facts": {
            "headline": "Rovers name teenager as first-choice keeper",
            "summary": "The 18-year-old will start against United on Saturday.",
            "publishedAt": "2008-12-06",
        },
        "sourceName": "The Guardian",
        "sourceDatasetRef": "https://www.theguardian.com/football/2008/dec/06/x",
        "status": "needs_review",
    }
    base.update(overrides)
    return base


def mc_fields(**overrides):
    base = {
        "type": "mc",
        "prompt": "Which club named an 18-year-old as its first-choice keeper?",
        "answer": "Rovers",
        "distractors": ["United", "City", "Wanderers"],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ citation

def test_cited_sentence_joins_headline_and_standfirst():
    # The standfirst is where the fact usually lives; a headline alone is
    # often a pun and cites nothing checkable.
    sentence = nd.cited_sentence(candidate())
    assert "first-choice keeper" in sentence
    assert "against United on Saturday" in sentence


def test_cited_sentence_survives_a_missing_standfirst():
    c = candidate(facts={"headline": "Rovers name teenager"})
    assert nd.cited_sentence(c) == "Rovers name teenager"


def test_candidate_with_nothing_to_cite_is_rejected():
    c = candidate(facts={}, title="")
    problems = nd.validate(mc_fields(), c)
    assert any("no sentence to cite" in p for p in problems)


def test_candidate_without_a_link_is_rejected():
    c = candidate(sourceDatasetRef=None)
    problems = nd.validate(mc_fields(), c)
    assert any("no source link" in p for p in problems)


# ---------------------------------------------------------------- validation

def test_a_well_formed_question_has_no_problems():
    assert nd.validate(mc_fields(), candidate()) == []


def test_answer_inside_the_prompt_is_caught():
    # Invisible while typing, and fatal once shipped.
    fields = mc_fields(
        prompt="Which club, Rovers or another, named an 18-year-old keeper?")
    problems = nd.validate(fields, candidate())
    assert any("answer appears in the prompt" in p for p in problems)


def test_multiple_choice_needs_enough_distractors():
    problems = nd.validate(mc_fields(distractors=["United"]), candidate())
    assert any("distractors" in p for p in problems)


def test_a_distractor_may_not_repeat_the_answer():
    fields = mc_fields(distractors=["United", "City", "rovers"])
    problems = nd.validate(fields, candidate())
    assert any("repeats the answer" in p for p in problems)


def test_duplicate_distractors_are_caught():
    fields = mc_fields(distractors=["United", "City", "city"])
    problems = nd.validate(fields, candidate())
    assert any("duplicate distractors" in p for p in problems)


def test_ordering_questions_cannot_be_hand_written():
    # An ordering question needs four dated items. An article has one date, so
    # offering the format would invite an answer nothing can check.
    fields = {"type": "ordering", "prompt": "Put these in order, earliest first.",
              "answer": ["a", "b", "c", "d"]}
    problems = nd.validate(fields, candidate())
    assert any("type must be one of" in p for p in problems)


def test_numeric_questions_need_a_number():
    fields = {"type": "numeric", "answer": "eighteen",
              "prompt": "How old was the keeper Rovers named?"}
    problems = nd.validate(fields, candidate())
    assert any("numeric answer" in p for p in problems)


def test_clue_ladder_must_not_contain_its_own_answer():
    fields = {
        "type": "clue",
        "prompt": "Who is this? Every clue you take is worth fewer points.",
        "answer": "Rovers",
        "clues": ["They play in blue.",
                  "Rovers named a teenager in goal.",
                  "It happened in December 2008."],
    }
    problems = nd.validate(fields, candidate())
    assert any("appears in its own clues" in p for p in problems)


def test_a_short_prompt_is_not_a_question():
    problems = nd.validate(mc_fields(prompt="Who?"), candidate())
    assert any("too short" in p for p in problems)


# ----------------------------------------------------------------- authoring

def test_provenance_comes_from_the_candidate_not_the_request(monkeypatch):
    """
    The caller supplies wording; the corpus supplies the citation.

    A request that tries to re-point a question at a different source must not
    be able to, or the citation becomes a claim rather than a fact.
    """
    written = {}

    class FakeTable:
        def put_item(self, Item):
            written.update(Item)

    monkeypatch.setattr(nd, "_questions", lambda: FakeTable())

    fields = mc_fields(
        sourceDatasetRef="https://example.com/not-the-source",
        sourceName="Somewhere Else",
        citedSentence="A sentence nobody wrote.",
    )
    item = nd.question_from_candidate(candidate(), fields, "dom@example.com")

    assert item["sourceDatasetRef"].startswith("https://www.theguardian.com/")
    assert item["sourceName"] == "The Guardian"
    assert "first-choice keeper" in item["citedSentence"]
    assert written["questionId"] == item["questionId"]


def test_authored_questions_land_approved_and_marked(monkeypatch):
    monkeypatch.setattr(nd, "_questions",
                        lambda: type("T", (), {"put_item": lambda s, Item: None})())
    item = nd.question_from_candidate(candidate(), mc_fields(), "dom@example.com")

    # Approved, because the review already happened - a person read the
    # sentence and typed the question.
    assert item["status"] == "approved"
    assert item["authoredBy"] == "dom@example.com"
    assert item["sportTier"] == "news#3"


def test_authoring_refuses_an_invalid_question(monkeypatch):
    monkeypatch.setattr(nd, "_questions",
                        lambda: type("T", (), {"put_item": lambda s, Item: None})())
    with pytest.raises(ValueError, match="answer appears in the prompt"):
        nd.question_from_candidate(
            candidate(),
            mc_fields(prompt="Did Rovers name an 18-year-old in goal that week?"),
            "dom@example.com")


def test_tier_follows_recency():
    assert nd._tier_for(2024) == 1
    assert nd._tier_for(2014) == 2
    assert nd._tier_for(2004) == 3
    # 1999 is the earliest the Guardian archive reaches, so the oldest band is
    # unreachable from this source by construction.
    assert nd._tier_for(1999) == 4


def test_question_id_is_stable_for_the_same_wording(monkeypatch):
    monkeypatch.setattr(nd, "_questions",
                        lambda: type("T", (), {"put_item": lambda s, Item: None})())
    a = nd.question_from_candidate(candidate(), mc_fields(), "dom@example.com")
    b = nd.question_from_candidate(candidate(), mc_fields(), "dom@example.com")
    assert a["questionId"] == b["questionId"]
