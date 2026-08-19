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


def typed(qid, tier, sport, qtype, mmdd="08-13", score=80):
    """A question with a chosen format, for the shape and rotation rules."""
    item = q(qid, tier, sport, mmdd, score=score)
    item["type"] = qtype
    return item


class TestOpeningQuestion:
    """
    Slot one used to be whatever tier 1 offered, and tier 1 covers 96 of 366
    dates — so 72% of published quizzes opened with a numeric question and half
    of them opened with the same sport. It is now chosen for rotation.
    """

    def _bank(self, mmdd):
        # Every opener format available in several sports, plus the two heavy
        # formats that should never be asked first.
        bank = []
        for i, (sport, qtype) in enumerate([
            ("mlb", "numeric"), ("soccer", "mc"), ("nhl", "multi"),
            ("f1", "numeric"), ("nba", "mc"), ("soccer", "multi"),
        ]):
            bank.append(typed(f"{mmdd}-o{i}", (i % 5) + 1, sport, qtype, mmdd))
        bank.append(typed(f"{mmdd}-map", 1, "mlb", "map", mmdd, score=99))
        bank.append(typed(f"{mmdd}-ord", 1, "nba", "ordering", mmdd, score=99))
        return bank

    def test_never_opens_on_a_format_that_must_be_learned_first(self):
        """
        A map or an ordering puzzle at question one asks the player to work out
        the interface before answering anything. Both here outscore every
        alternative on notability and must still lose the opening slot.
        """
        r = asm.assemble("2026-08-13", self._bank("08-13"))
        assert r.questions[0]["type"] in asm.OPENER_TYPES

    def test_consecutive_days_do_not_open_on_the_same_sport(self):
        bank = self._bank("08-13") + self._bank("08-14") + self._bank("08-15")
        results = asm.assemble_range(
            ["2026-08-13", "2026-08-14", "2026-08-15"], bank)
        openers = [r.questions[0]["sport"] for r in results]
        assert len(set(openers)) == len(openers), openers

    def test_consecutive_days_do_not_open_on_the_same_format(self):
        bank = self._bank("08-13") + self._bank("08-14") + self._bank("08-15")
        results = asm.assemble_range(
            ["2026-08-13", "2026-08-14", "2026-08-15"], bank)
        formats = [r.questions[0]["type"] for r in results]
        assert len(set(formats)) == len(formats), formats

    def test_rotation_carries_into_a_later_run(self):
        """
        A fresh assembly run must not repeat the format the previous run ended
        on, or a week assembled on Monday opens the same way as the one before.
        """
        bank = self._bank("08-13")
        seeded = asm.assemble("2026-08-13", bank,
                              recent_openers=[("numeric", "mlb")])
        assert seeded.questions[0]["type"] != "numeric"

    def test_a_date_with_no_suitable_opener_still_produces_a_quiz(self):
        """Rotation is a preference. It must not cost the day its quiz."""
        bank = [typed(f"m{t}", t, "mlb", "map", "09-09") for t in range(1, 6)]
        r = asm.assemble("2026-09-09", bank)
        assert r.complete


class TestSportCap:
    """
    The bank is 84% baseball because Retrosheet reaches 1871 while the other
    sources start in the 1990s. Without a ceiling the deepest archive wins every
    slot on merit.
    """

    def test_one_sport_cannot_take_the_whole_quiz_when_others_are_available(self):
        bank = [typed(f"b{t}", t, "mlb", "mc", "08-13") for t in range(1, 6)]
        bank += [typed(f"s{t}", t, "soccer", "numeric", "08-13") for t in range(1, 6)]
        bank += [typed(f"n{t}", t, "nhl", "multi", "08-13") for t in range(1, 6)]
        r = asm.assemble("2026-08-13", bank)
        counts = collections.Counter(x["sport"] for x in r.questions)
        assert counts["mlb"] <= asm.MAX_PER_SPORT

    def test_a_single_sport_date_still_fills_and_says_so(self):
        """Scarcity is a content problem to report, not a rule to fail on."""
        bank = [typed(f"b{t}", t, "mlb", "mc", "08-13") for t in range(1, 6)]
        r = asm.assemble("2026-08-13", bank)
        assert r.complete
        assert "sport-cap" in r.relaxed

    def test_format_repetition_is_conceded_before_sport_balance(self):
        """
        A second numeric question is duller; a fourth baseball question is the
        complaint players actually voice. Everything outside baseball here
        shares one format, so filling the quiz means repeating that format —
        and it should, rather than reaching for more mlb.

        Three sports, because a cap of two per sport across five questions is
        only satisfiable with three of them. Two sports and a cap of two is an
        impossible ask, and the assembler is right to break it.
        """
        bank = [typed(f"b{t}", t, "mlb", "mc", "08-13") for t in range(1, 6)]
        bank += [typed(f"s{i}", i + 1, "soccer", "numeric", "08-13") for i in range(3)]
        bank += [typed(f"n{i}", i + 2, "nhl", "numeric", "08-13") for i in range(3)]
        r = asm.assemble("2026-08-13", bank)
        counts = collections.Counter(x["sport"] for x in r.questions)
        assert counts["mlb"] <= asm.MAX_PER_SPORT
        assert r.complete

    def test_the_cap_is_only_broken_when_the_date_cannot_meet_it(self):
        """
        Two sports cannot fill five slots at two apiece. Breaking the cap is
        correct there — what matters is that it is reported rather than passed
        off as a balanced quiz.
        """
        bank = [typed(f"b{t}", t, "mlb", "mc", "08-13") for t in range(1, 6)]
        bank += [typed(f"s{i}", 3, "soccer", "numeric", "08-13") for i in range(4)]
        r = asm.assemble("2026-08-13", bank)
        assert r.complete
        assert "sport-cap" in r.relaxed


class TestQuizShape:
    def test_it_settles_on_a_few_formats_rather_than_a_new_one_each_time(self):
        """
        Five questions in five different interactions meant learning a new
        control five times in three minutes. Content variety is the point;
        interface variety is not.
        """
        bank = []
        for i, t in enumerate(["mc", "numeric", "multi", "map", "ordering", "clue"]):
            for tier in range(1, 6):
                bank.append(typed(f"{t}{tier}", tier, ["mlb", "soccer", "nhl"][i % 3],
                                  t, "08-13"))
        r = asm.assemble("2026-08-13", bank)
        assert len({x["type"] for x in r.questions}) <= asm.TARGET_DISTINCT_FORMATS + 1

    def test_the_opener_stays_first_even_when_its_tier_would_not(self):
        bank = [typed("late", 5, "soccer", "mc", "08-13", score=99)]
        bank += [typed(f"m{t}", t, "mlb", "map", "08-13") for t in range(1, 5)]
        r = asm.assemble("2026-08-13", bank)
        assert r.questions[0]["questionId"] == "late"


class TestReplacePublishedGuard:
    """
    The one path allowed to overwrite a published quiz. It is narrow on
    purpose, so the guard is worth testing rather than trusting.
    """

    def _item(self, quiz_date):
        return {"quizDate": quiz_date, "questionIds": ["a", "b", "c", "d", "e"]}

    def test_it_refuses_a_day_that_is_being_played(self, monkeypatch):
        """
        Today is excluded on purpose: a quiz that changes under a player
        part-way through their run is worse than a repetitive one.
        """
        from lambdas.common import quizzes_dynamo

        with pytest.raises(ValueError, match="not in the future"):
            quizzes_dynamo.replace_published(
                self._item("2026-08-19"), today="2026-08-19")

    def test_it_refuses_a_day_already_played(self):
        from lambdas.common import quizzes_dynamo

        with pytest.raises(ValueError, match="not in the future"):
            quizzes_dynamo.replace_published(
                self._item("2026-08-01"), today="2026-08-19")

    def test_a_future_day_is_written_back_still_published(self, monkeypatch):
        """
        Rebuilt days must not drop to draft, or replacing them punches a hole
        in the runway that decides whether the game is playable next month.
        """
        from lambdas.common import quizzes_dynamo

        written = {}

        class _T:
            def put_item(self, Item):
                written.update(Item)

        monkeypatch.setattr(quizzes_dynamo, "_table", lambda: _T())
        out = quizzes_dynamo.replace_published(
            self._item("2026-09-30"), today="2026-08-19")

        assert out["status"] == "published"
        assert written["status"] == "published"
        assert "rebuiltAt" in written


class TestNoDuplicateQuestionShapes:
    """
    Settling the format mix made a second kind of repetition visible: a quiz
    can hold three formats and still ask the same question twice.
    """

    def test_only_one_clue_ladder_per_quiz(self):
        """
        Two clue ladders means two questions whose prompts both read "Who is
        this?", which is the most literal repetition the game can produce.
        """
        bank = [typed(f"c{t}", t, "mlb", "clue", "08-13") for t in range(1, 6)]
        bank += [typed(f"m{t}", t, "soccer", "mc", "08-13") for t in range(1, 6)]
        bank += [typed(f"n{t}", t, "nhl", "numeric", "08-13") for t in range(1, 6)]
        r = asm.assemble("2026-08-13", bank)
        clues = [x for x in r.questions if x["type"] == "clue"]
        assert len(clues) <= asm.MAX_CLOSERS

    def test_it_avoids_two_questions_of_the_same_sport_and_format(self):
        """
        Two soccer numeric questions are the same question twice — the live
        symptom was two German second division scorelines back to back.

        Each sport offers two formats here, so five slots can be filled without
        doubling a pairing. With only one format on offer a repeat is forced,
        which is why this is a preference and not a gate.
        """
        bank = []
        for sport in ("soccer", "mlb", "nhl"):
            for t in range(1, 6):
                bank.append(typed(f"{sport}n{t}", t, sport, "numeric", "08-13"))
                bank.append(typed(f"{sport}m{t}", t, sport, "mc", "08-13"))
        r = asm.assemble("2026-08-13", bank)
        pairs = collections.Counter((x["sport"], x["type"]) for x in r.questions)
        assert max(pairs.values()) == 1, pairs

    def test_a_repeat_is_still_taken_over_a_short_quiz(self):
        """The pairing rule is a preference. It must not cost the day a slot."""
        bank = [typed(f"s{t}", t, "soccer", "numeric", "08-13") for t in range(1, 6)]
        r = asm.assemble("2026-08-13", bank)
        assert r.complete
