"""
Quiz assembly tests.

The assembler's job is to hold three constraints in priority order and give up
the right one first. Most of these check what it refuses to do: repeat a
question on a date a returning player has already seen, or promote an unapproved
question to fill a gap.
"""

import collections

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


class TestFormatMix:
    """
    Sport variety alone does not stop a quiz being five multiple-choice prompts
    in a row, and format monotony is what makes a daily game feel identical on
    day two. The cap yields — but only after the polite sweep runs out.
    """

    def _bank(self, types, mmdd="08-13"):
        out = []
        for i, t in enumerate(types, start=1):
            item = q(f"q{i}", min(i, 5), sport=f"s{i}", mmdd=mmdd)
            item["type"] = t
            out.append(item)
        return out

    def test_a_varied_bank_produces_a_varied_quiz(self):
        bank = self._bank(["mc", "numeric", "ordering", "clue", "mc"])
        r = asm.assemble("2026-08-13", bank)

        counts = collections.Counter(x["type"] for x in r.questions)
        assert max(counts.values()) <= asm.MAX_PER_TYPE
        assert "format-mix" not in r.relaxed

    def test_the_cap_scales_to_the_variety_the_bank_has(self):
        """
        Two formats cannot fill five slots at two apiece. A fixed cap would mark
        every quiz relaxed and drown the days that genuinely went wrong.
        """
        assert asm._type_cap(self._bank(["mc"] * 5)) == 5          # one format
        assert asm._type_cap(self._bank(["mc", "numeric"])) == 3   # two
        assert asm._type_cap(
            self._bank(["mc", "numeric", "ordering"])) == 2        # three
        assert asm._type_cap(
            self._bank(["mc", "numeric", "ordering", "clue", "map"])) == 2

    def test_no_single_format_dominates_when_variety_exists(self):
        # Six mc and three numeric: a naive picker would take five mc.
        bank = self._bank(["mc"] * 6 + ["numeric"] * 3)
        r = asm.assemble("2026-08-13", bank)

        counts = collections.Counter(x["type"] for x in r.questions)
        assert counts["mc"] <= asm._type_cap(bank)
        assert counts["numeric"] >= 1, "a second format was available and unused"

    def test_a_single_format_bank_still_produces_a_full_quiz(self):
        """
        Five multiple-choice questions is the honest best a single-format bank
        can do, so it is not reported as a relaxed constraint - only as a
        content observation.
        """
        bank = self._bank(["mc"] * 5)
        r = asm.assemble("2026-08-13", bank)

        assert len(r.questions) == asm.QUIZ_LENGTH
        assert "format-mix" not in r.relaxed

    def test_a_closer_is_placed_last_regardless_of_tier(self):
        bank = self._bank(["clue", "mc", "numeric", "ordering", "mc"])
        r = asm.assemble("2026-08-13", bank)

        # The clue question is tier 1 and would otherwise open the quiz.
        assert r.questions[-1]["type"] == "clue"

    def test_the_format_mix_is_reported(self):
        bank = self._bank(["mc", "numeric", "ordering", "clue", "mc"])
        item = asm.assemble("2026-08-13", bank).to_item()
        assert item["formatMix"]["mc"] == 2


class TestLeapDay:
    """
    February 29 has occurred a quarter as often as any other date, so the
    corpus holds three approved questions for it against a five-question quiz —
    and publishing refuses a short quiz. Without a fallback, 2028-02-29 would
    have had no quiz at all.
    """

    def _q(self, i, mmdd, tier):
        return {"questionId": f"q{i}", "status": "approved", "mmdd": mmdd,
                "type": ["mc", "numeric", "clue", "map", "ordering"][i % 5],
                "tier": tier, "sport": ["mlb", "nhl", "f1", "nba", "soccer"][i % 5],
                "prompt": f"prompt {i}", "answer": "a"}

    def test_a_leap_day_borrows_from_february_28(self):
        from lambdas.common.assembler import assemble
        questions = ([self._q(i, "02-29", (i % 5) + 1) for i in range(3)]
                     + [self._q(10 + i, "02-28", (i % 5) + 1) for i in range(10)])
        result = assemble("2028-02-29", questions)
        assert result.complete
        assert "leap-day-borrow" in result.relaxed
        assert any("february 28" in w for w in result.warnings)

    def test_the_borrowing_is_visible_rather_than_silent(self):
        # The schedule panel has to be able to show that a day is not built
        # from its own date, or it presents the 28th's questions as the 29th's.
        from lambdas.common.assembler import assemble
        questions = ([self._q(i, "02-29", (i % 5) + 1) for i in range(3)]
                     + [self._q(10 + i, "02-28", (i % 5) + 1) for i in range(10)])
        assert assemble("2028-02-29", questions).relaxed

    def test_a_leap_day_with_enough_of_its_own_does_not_borrow(self):
        from lambdas.common.assembler import assemble
        questions = ([self._q(i, "02-29", (i % 5) + 1) for i in range(8)]
                     + [self._q(50 + i, "02-28", (i % 5) + 1) for i in range(5)])
        result = assemble("2028-02-29", questions)
        assert "leap-day-borrow" not in result.relaxed
        assert all(q["mmdd"] == "02-29" for q in result.questions)

    def test_no_other_date_borrows(self):
        # A thin July date stays thin and gets flagged; only the leap day, which
        # cannot fill itself by construction, is allowed to reach next door.
        from lambdas.common.assembler import assemble
        questions = ([self._q(i, "07-04", (i % 5) + 1) for i in range(2)]
                     + [self._q(20 + i, "07-03", (i % 5) + 1) for i in range(10)])
        result = assemble("2027-07-04", questions)
        assert not result.complete
        assert "leap-day-borrow" not in result.relaxed

    def test_the_days_own_questions_are_used_before_borrowed_ones(self):
        """
        The first run of this filled all five slots from February 28 while the
        three real February 29 questions sat unused — the borrowed pool was
        simply larger and won on every other tiebreak. A borrowed question is a
        fallback, not a peer.
        """
        from lambdas.common.assembler import assemble
        questions = ([self._q(i, "02-29", (i % 5) + 1) for i in range(3)]
                     + [self._q(10 + i, "02-28", (i % 5) + 1) for i in range(20)])
        result = assemble("2028-02-29", questions)
        own = [q for q in result.questions if q["mmdd"] == "02-29"]
        assert len(own) == 3, "every real leap-day question should be used"

    def test_the_borrowed_marker_never_reaches_the_stored_quiz(self):
        from lambdas.common.assembler import assemble
        questions = ([self._q(i, "02-29", (i % 5) + 1) for i in range(3)]
                     + [self._q(10 + i, "02-28", (i % 5) + 1) for i in range(10)])
        result = assemble("2028-02-29", questions)
        assert all("_borrowed" not in q for q in questions)
        assert "questionIds" in result.to_item()
