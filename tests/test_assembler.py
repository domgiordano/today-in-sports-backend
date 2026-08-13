"""
Quiz assembly tests.

The assembler's job is to hold three constraints in priority order and give up
the right one first. Most of these check what it refuses to do: repeat a
question on a date a returning player has already seen, or promote an unapproved
question to fill a gap.
"""

import pytest

from lambdas.common import assembler as asm


def q(qid, tier, sport="mlb", mmdd="08-13", status="approved", score=80):
    return {
        "questionId": qid,
        "tier": tier,
        "sport": sport,
        "mmdd": mmdd,
        "status": status,
        "notabilityScore": score,
        "prompt": f"question {qid}",
        "type": "mc",
    }


def full_bank(mmdd="08-13"):
    """One question per tier across five different sports."""
    sports = ["mlb", "nhl", "nfl", "f1", "mlb"]
    return [q(f"q{t}", t, sports[t - 1], mmdd) for t in range(1, 6)]


class TestHappyPath:
    def test_builds_five_in_ascending_tier_order(self):
        r = asm.assemble("2026-08-13", full_bank())
        assert r.complete
        assert [x["tier"] for x in r.questions] == [1, 2, 3, 4, 5]
        assert not r.relaxed

    def test_is_deterministic(self):
        """Same bank and date must always yield the same quiz."""
        bank = full_bank()
        a = asm.assemble("2026-08-13", bank).to_item()
        b = asm.assemble("2026-08-13", list(reversed(bank))).to_item()
        assert a["questionIds"] == b["questionIds"]

    def test_prefers_spreading_across_sports(self):
        """Given a choice within a tier, take the unrepresented sport."""
        bank = [
            q("t1", 1, "mlb"), q("t2", 2, "mlb"), q("t3", 3, "mlb"),
            q("t4a", 4, "mlb"), q("t4b", 4, "nhl"),
            q("t5a", 5, "mlb"), q("t5b", 5, "nfl"),
        ]
        r = asm.assemble("2026-08-13", bank)
        picked = {x["questionId"] for x in r.questions}
        assert "t4b" in picked and "t4a" not in picked
        assert "t5b" in picked and "t5a" not in picked


class TestConstraintsItWillNotBreak:
    def test_never_reuses_a_question_seen_on_this_date(self):
        """
        The premise is date-anchored, so August 13 returns every year. A player
        who saw a question last year must not see it again.
        """
        bank = full_bank()
        used = {"q1", "q3"}
        r = asm.assemble("2026-08-13", bank, used_ids=used)
        assert not (used & {x["questionId"] for x in r.questions})

    def test_never_promotes_an_unapproved_question(self):
        """A gap is a content problem to surface, not a rule to bend."""
        bank = [q("ok", 1)] + [q(f"draft{t}", t, status="draft") for t in range(2, 6)]
        r = asm.assemble("2026-08-13", bank)
        assert len(r.questions) == 1
        assert r.questions[0]["questionId"] == "ok"
        assert not r.complete
        assert any("short" in w for w in r.warnings)

    def test_ignores_other_calendar_dates(self):
        bank = full_bank("08-13") + full_bank("09-01")
        r = asm.assemble("2026-08-13", bank)
        assert all(x["mmdd"] == "08-13" for x in r.questions)


class TestDegradation:
    def test_fills_missing_tiers_from_adjacent_ones(self):
        """A five-question quiz with an imperfect ladder beats a four-question one."""
        bank = [q("a", 1), q("b", 1), q("c", 2), q("d", 2), q("e", 5)]
        r = asm.assemble("2026-08-13", bank)
        assert r.complete
        assert "tier-ladder" in r.relaxed
        assert [x["tier"] for x in r.questions] == sorted(x["tier"] for x in r.questions)

    def test_relaxes_sport_mix_on_a_baseball_only_date(self):
        """Many July dates have nothing but baseball. Still ship a quiz."""
        bank = [q(f"q{t}", t, "mlb", mmdd="07-04") for t in range(1, 6)]
        r = asm.assemble("2026-07-04", bank)
        assert r.complete
        assert "sport-mix" in r.relaxed
        assert any("sport" in w for w in r.warnings)

    def test_empty_date_reports_rather_than_raising(self):
        r = asm.assemble("2026-02-14", [])
        assert r.questions == []
        assert not r.complete
        assert r.warnings

    def test_item_records_what_was_relaxed(self):
        bank = [q(f"q{t}", t, "mlb", mmdd="07-04") for t in range(1, 6)]
        item = asm.assemble("2026-07-04", bank).to_item()
        assert item["quizDate"] == "2026-07-04"
        assert len(item["questionIds"]) == 5
        assert item["sportMix"] == {"mlb": 5}
        assert item["tierLadder"] == [1, 2, 3, 4, 5]
        assert "sport-mix" in item["relaxedConstraints"]


class TestCoverageReport:
    def test_counts_shortfalls(self):
        bank = full_bank("08-13") + [q("x", 1, mmdd="09-01")]
        rep = asm.coverage_report(bank)
        assert rep["approvedTotal"] == 6
        assert rep["datesCovered"] == 2
        assert rep["datesUnder5"] == 1      # 09-01 has a single question
        assert rep["datesEmpty"] == 364

    def test_ignores_unapproved(self):
        rep = asm.coverage_report([q("d", 1, status="draft")])
        assert rep["approvedTotal"] == 0
        assert rep["datesCovered"] == 0


class TestSameEvent:
    """
    Two questions built from one event must not share a quiz.

    They have different ids, so deduping on questionId alone lets them through,
    and for transactions one routinely answers the other: the sale-price
    question names the buying club that the destination question asks for.
    """

    def _pair(self):
        a = q("a", 1)
        b = q("b", 2)
        a["sourceEventId"] = b["sourceEventId"] = "tran-46087"
        return [a, b]

    def test_a_second_question_from_the_same_event_is_not_chosen(self):
        bank = self._pair() + [q("c", 3), q("d", 4), q("e", 5)]
        r = asm.assemble("2026-08-13", bank)

        events = [x.get("sourceEventId") for x in r.questions]
        assert events.count("tran-46087") == 1

    def test_questions_without_an_event_id_do_not_block_each_other(self):
        """
        A missing id is not a shared id. Treating None as one would collapse
        every question lacking provenance into a single slot.
        """
        r = asm.assemble("2026-08-13", full_bank())
        assert len(r.questions) == asm.QUIZ_LENGTH
