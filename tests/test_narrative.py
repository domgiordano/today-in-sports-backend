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


# ------------------------------------------- candidates are not raw material

def news_event(year, headline):
    return {
        "sport": "news", "league": "Football", "reason": "narrative_event",
        "gameId": f"news-{year}", "gameDate": f"{year}-08-14",
        "year": year, "mmdd": "08-14",
        "title": headline, "facts": {"headline": headline},
        "sourceName": "The Guardian",
        "sourceDatasetRef": "https://www.theguardian.com/x",
        "status": "needs_review",
    }


def test_narrative_candidates_never_become_ordering_questions():
    """
    Ordering is the one template built over every event regardless of sport,
    which let a Guardian headline become a drag-to-order item shown to a player
    verbatim — no citation, no reviewer, and against the rule that a sentence
    is only ever restated beside the question a human wrote from it.
    """
    from lambdas.common.templates import ordering_templates as ord_tpl

    events = [
        news_event(2001, "Rovers name teenager as first-choice keeper"),
        news_event(2005, "Manager sacked after cup exit"),
        news_event(2011, "Striker signs for a record fee"),
        news_event(2016, "Captain retires from international duty"),
        news_event(2020, "Season suspended after outbreak"),
    ]
    assert ord_tpl.generate(events, {}) == []


def test_a_written_candidate_is_still_not_raw_material():
    # The wording stays the newspaper's after a human has used it, so the
    # exclusion is by sport rather than by where the candidate is in its life.
    from lambdas.common.templates import ordering_templates as ord_tpl

    events = [news_event(2000 + i * 5, f"Something happened number {i}")
              for i in range(5)]
    for e in events:
        e["status"] = "written"
    assert ord_tpl.generate(events, {}) == []


def test_derivable_events_still_produce_ordering_questions():
    # The exclusion must not quietly take the real corpus with it.
    from lambdas.common.templates import ordering_templates as ord_tpl

    events = [{
        "sport": "mlb", "league": "AL", "reason": "no_hitter",
        "gameId": f"g{y}", "gameDate": f"{y}-08-14", "year": y, "mmdd": "08-14",
        "title": f"A pitcher no-hit the opposition, game {y % 100}",
        "facts": {}, "sourceName": "Retrosheet", "sourceDatasetRef": "r",
    } for y in (1965, 1972, 1984, 1999)]
    assert len(ord_tpl.generate(events, {})) == 1


def test_the_two_narrative_sport_constants_agree():
    # Two modules name the same sport code. If they drift, the ordering
    # exclusion silently stops matching and the leak comes back.
    from lambdas.common.templates import ordering_templates as ord_tpl
    assert ord_tpl.NARRATIVE_SPORT == nd.NARRATIVE_SPORT


def test_the_queue_ranks_the_whole_partition_not_the_first_page(monkeypatch):
    """
    The listing stopped paginating once it had `limit` rows and sorted those.
    With a backfilled archive that sorts an arbitrary early page: the best
    candidate sits unseen on page two hundred and the panel looks broken.
    """
    pages = [
        {"Items": [dict(candidate(), gameId=f"a{i}", candidateScore=8)
                   for i in range(50)],
         "LastEvaluatedKey": {"k": 1}},
        {"Items": [dict(candidate(), gameId="best", candidateScore=30)]},
    ]

    class FakeTable:
        def query(self, **kwargs):
            return pages[1] if kwargs.get("ExclusiveStartKey") else pages[0]

    monkeypatch.setattr(nd, "_events", lambda: FakeTable())
    top = nd.list_candidates(limit=5)
    assert top[0]["gameId"] == "best"


def test_a_machine_drafted_question_lands_as_draft(monkeypatch):
    """
    The rule permits restating a sentence; what it forbids is asserting a fact
    the sentence does not carry. But hand-written questions skip review because
    a person already read the source, and that reason does not hold for a
    restatement — so those go to the queue like anything else.
    """
    monkeypatch.setattr(nd, "_questions",
                        lambda: type("T", (), {"put_item": lambda s, Item: None})())
    item = nd.question_from_candidate(
        candidate(), mc_fields(), "claude-code", machine_authored=True)
    assert item["status"] == "draft"
    assert item["machineAuthored"] is True
    # The citation still travels with it — that property is not negotiable.
    assert "first-choice keeper" in item["citedSentence"]


def test_a_hand_written_question_is_still_approved(monkeypatch):
    monkeypatch.setattr(nd, "_questions",
                        lambda: type("T", (), {"put_item": lambda s, Item: None})())
    item = nd.question_from_candidate(candidate(), mc_fields(), "dom@example.com")
    assert item["status"] == "approved"
    assert item["machineAuthored"] is False


def test_a_restated_question_is_never_auto_approved():
    """
    Every rule in auto_review is arithmetic over a question's own fields, and
    none of them can tell whether a restatement is faithful to its source.
    Without an explicit hold these pass cleanly and get approved on the next
    run, undoing the reason they were drafted as drafts.
    """
    import importlib.util, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "auto_review.py"
    spec = importlib.util.spec_from_file_location("auto_review", path)
    ar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ar)

    q = {"type": "numeric", "sport": "news", "year": 2000,
         "prompt": "Denis Irwin retired on this day in 2000. How many caps had "
                   "he won for the Republic of Ireland?",
         "numericAnswer": 56, "answer": 56, "machineAuthored": True}
    flags = ar.flags_for(q)
    assert any("check it against the sentence" in f for f in flags)

    # And the same question typed by a person is not held.
    assert ar.flags_for(dict(q, machineAuthored=False)) == []
