"""
Scoring tests.

The first class is the one that matters. Everything else is calibration; that
one is the product promise — this is a game about knowing sport, not about
reflexes, and it stops being that the moment a fast guess outscores a
considered right answer.
"""

import pytest

from lambdas.common import scoring


def mc(tier=3, answer="Nolan Ryan"):
    return {"type": "mc", "tier": tier, "answer": answer,
            "distractors": ["A", "B", "C"]}


def numeric(tier=3, answer=19, tolerance=2):
    return {"type": "numeric", "tier": tier, "answer": answer,
            "numericAnswer": answer, "tolerance": tolerance}


class TestFastWrongNeverBeatsSlowRight:
    """The single property the whole design hangs on."""

    def test_instant_wrong_scores_below_slow_right(self):
        fast_wrong = scoring.grade(mc(), "A", seconds=0.5)
        slow_right = scoring.grade(mc(), "Nolan Ryan", seconds=120)
        assert fast_wrong["points"] < slow_right["points"]

    def test_a_wrong_answer_earns_nothing_at_all(self):
        assert scoring.grade(mc(), "A", seconds=0.1)["points"] == 0

    def test_holds_across_every_tier_pairing(self):
        """Even a tier-5 miss must lose to a tier-1 hit taken slowly."""
        for wrong_tier in (1, 2, 3, 4, 5):
            fast_wrong = scoring.grade(mc(tier=wrong_tier), "A", seconds=0.1)
            slow_right = scoring.grade(mc(tier=1), "Nolan Ryan", seconds=300)
            assert fast_wrong["points"] < slow_right["points"]

    def test_a_partial_answer_loses_to_an_exact_one_however_fast(self):
        """
        Note the guess has to be *outside* tolerance to be partial at all. An
        answer within tolerance is correct by definition, so beating a slower
        correct answer is the time bonus doing its job, not a violation.
        """
        partial_fast = scoring.grade(numeric(), 24, seconds=1)   # tolerance is 2
        exact_slow = scoring.grade(numeric(), 19, seconds=90)
        assert partial_fast["credit"] < 1.0, "guess should be outside tolerance"
        assert partial_fast["points"] < exact_slow["points"]

    def test_within_tolerance_counts_as_correct(self):
        """Tolerance is the whole point of a closest-guess question."""
        assert scoring.grade(numeric(), 21, seconds=5)["correct"] is True


class TestTimeBonus:
    def test_full_inside_the_grace_window(self):
        base = scoring.base_value(3)
        assert scoring.time_bonus(base, 0) == scoring.time_bonus(base, 9.9)

    def test_decays_after_the_grace_window(self):
        base = scoring.base_value(3)
        assert scoring.time_bonus(base, 20) < scoring.time_bonus(base, 5)

    def test_never_reaches_zero(self):
        """A long think should cost a little, not everything."""
        base = scoring.base_value(3)
        assert scoring.time_bonus(base, 600) > 0
        assert scoring.time_bonus(base, 10_000) > 0

    def test_capped_so_speed_cannot_out_earn_correctness(self):
        base = scoring.base_value(5)
        assert scoring.time_bonus(base, 0) <= base * scoring.MAX_BONUS_FRACTION

    def test_missing_timing_is_treated_as_slow_not_instant(self):
        """A client that reports nothing must not be rewarded for it."""
        base = scoring.base_value(3)
        assert scoring.time_bonus(base, None) < scoring.time_bonus(base, 0)


class TestNumericPartialCredit:
    def test_exact_is_full(self):
        assert scoring.numeric_credit(19, 19, 2) == 1.0

    def test_inside_tolerance_is_full(self):
        assert scoring.numeric_credit(19, 21, 2) == 1.0
        assert scoring.numeric_credit(19, 17, 2) == 1.0

    def test_credit_falls_away_with_distance(self):
        near = scoring.numeric_credit(19, 24, 2)
        far = scoring.numeric_credit(19, 30, 2)
        assert 0 < near
        assert near > far

    def test_far_enough_is_worthless(self):
        assert scoring.numeric_credit(19, 500, 2) == 0.0

    def test_zero_tolerance_still_rewards_being_close(self):
        """
        Otherwise "off by one" and "off by fifty" score identically, which
        defeats the purpose of asking for a number.
        """
        close = scoring.numeric_credit(300, 301, 0)
        far = scoring.numeric_credit(300, 350, 0)
        assert close > far

    def test_a_non_numeric_guess_scores_zero_rather_than_raising(self):
        assert scoring.numeric_credit(19, "nineteen", 2) == 0.0
        assert scoring.numeric_credit(19, None, 2) == 0.0


class TestGrading:
    def test_multiple_choice_is_case_and_space_insensitive(self):
        assert scoring.grade(mc(), "  nolan ryan  ", seconds=5)["correct"] is True

    def test_higher_tiers_are_worth_more(self):
        low = scoring.grade(mc(tier=1), "Nolan Ryan", seconds=5)
        high = scoring.grade(mc(tier=5), "Nolan Ryan", seconds=5)
        assert high["points"] > low["points"]

    def test_partial_credit_scales_the_bonus_too(self):
        """A half-right answer should not collect a whole speed bonus."""
        partial = scoring.grade(numeric(), 24, seconds=1)
        full = scoring.grade(numeric(), 19, seconds=1)
        assert 0 < partial["timeBonus"] < full["timeBonus"]

    def test_no_answer_scores_zero(self):
        assert scoring.grade(mc(), None, seconds=5)["points"] == 0

    def test_result_explains_itself(self):
        r = scoring.grade(numeric(), 20, seconds=12)
        assert r["points"] == r["accuracyPoints"] + r["timeBonus"]
        assert 0 <= r["credit"] <= 1
        assert r["basePoints"] == scoring.base_value(3)


class TestMaxPossible:
    def test_perfect_run_is_the_sum_of_base_plus_full_bonus(self):
        questions = [mc(tier=t) for t in (1, 2, 3, 4, 5)]
        best = scoring.max_possible(questions)
        actual = sum(scoring.grade(q, q["answer"], seconds=1)["points"]
                     for q in questions)
        assert actual == best

    def test_no_real_run_can_exceed_it(self):
        questions = [mc(tier=t) for t in (1, 2, 3, 4, 5)]
        best = scoring.max_possible(questions)
        for secs in (0, 0.1, 5, 30, 120):
            total = sum(scoring.grade(q, q["answer"], seconds=secs)["points"]
                        for q in questions)
            assert total <= best


class TestHintLadder:
    """
    Multiple choice becomes a hint rather than the format. Recognition is an
    easier act than recall and the scoring says so - but taking the hint must
    still be worth doing, or nobody stuck on a question ever answers.
    """

    def _q(self, tier=3):
        return {"type": "mc", "tier": tier, "answer": "Nolan Ryan"}

    def test_the_hint_costs_a_fraction_of_the_credit(self):
        clean = scoring.grade(self._q(), "Nolan Ryan", 5.0)
        hinted = scoring.grade(self._q(), "Nolan Ryan", 5.0, hint_used=True)

        assert hinted["points"] < clean["points"]
        assert hinted["credit"] == round(scoring.HINT_CREDIT, 3)
        assert hinted["correct"] is True, "it was still the right answer"

    def test_taking_the_hint_still_beats_getting_it_wrong(self):
        hinted = scoring.grade(self._q(), "Nolan Ryan", 5.0, hint_used=True)
        wrong = scoring.grade(self._q(), "Bruce Hurst", 5.0)
        assert hinted["points"] > wrong["points"]

    def test_the_hint_does_not_rescue_a_wrong_answer(self):
        r = scoring.grade(self._q(), "Bruce Hurst", 5.0, hint_used=True)
        assert r["points"] == 0
        assert r["correct"] is False

    def test_a_typed_answer_is_matched_generously(self):
        """A surname, a missing accent or one typo is still the right answer."""
        for typed in ("Ryan", "ryan", "Nolan Ryan", "Nolan Rian"):
            assert scoring.grade(self._q(), typed, 5.0)["correct"], typed

    def test_the_hint_flag_is_reported(self):
        assert scoring.grade(self._q(), "Ryan", 5.0)["hintUsed"] is False
        assert scoring.grade(self._q(), "Ryan", 5.0, True)["hintUsed"] is True

    def test_a_fast_hinted_answer_never_beats_a_slow_unaided_one(self):
        """
        The founding rule of this module, extended to the hint: help must cost
        more than time saves, or the ladder becomes free.
        """
        fast_hinted = scoring.grade(self._q(), "Ryan", 0.0, hint_used=True)
        slow_clean = scoring.grade(self._q(), "Ryan", 120.0)
        assert slow_clean["points"] > fast_hinted["points"]


class TestOrderingCredit:
    """
    Per-pair credit, not all-or-nothing. One transposition in four items is
    five of six pairs right, and calling that zero would make the format feel
    arbitrary rather than hard.
    """

    ITEMS = ["a", "b", "c", "d"]

    def test_a_perfect_order_is_full_credit(self):
        assert scoring.ordering_credit(self.ITEMS, ["a", "b", "c", "d"]) == 1.0

    def test_a_fully_reversed_order_earns_nothing(self):
        assert scoring.ordering_credit(self.ITEMS, ["d", "c", "b", "a"]) == 0.0

    def test_one_swapped_pair_keeps_most_of_the_credit(self):
        credit = scoring.ordering_credit(self.ITEMS, ["b", "a", "c", "d"])
        assert credit == pytest.approx(5 / 6)

    def test_only_a_perfect_order_counts_as_correct(self):
        q = {"type": "ordering", "tier": 3, "answer": self.ITEMS}
        assert scoring.grade(q, ["a", "b", "c", "d"], 5.0)["correct"] is True
        assert scoring.grade(q, ["b", "a", "c", "d"], 5.0)["correct"] is False

    def test_partial_credit_still_scores_points(self):
        q = {"type": "ordering", "tier": 3, "answer": self.ITEMS}
        assert scoring.grade(q, ["b", "a", "c", "d"], 5.0)["points"] > 0

    @pytest.mark.parametrize("submitted", [
        ["a", "b", "c"],            # too few
        ["a", "b", "c", "z"],       # not the same items
        "abcd",                     # not a list
        None,
    ])
    def test_anything_that_is_not_a_permutation_earns_nothing(self, submitted):
        assert scoring.ordering_credit(self.ITEMS, submitted) == 0.0


class TestClueLadder:
    """The decay is the credit, so the ladder needs no grading of its own."""

    def test_answering_on_the_first_clue_is_full_value(self):
        assert scoring.clue_credit(0, 5) == 1.0

    def test_each_clue_costs_and_the_order_is_monotonic(self):
        values = [scoring.clue_credit(n, 5) for n in range(5)]
        assert values == sorted(values, reverse=True)

    def test_the_last_clue_is_still_worth_playing_for(self):
        """A question that becomes worthless is one people stop finishing."""
        assert scoring.clue_credit(99, 5) == scoring.CLUE_FLOOR
        assert scoring.CLUE_FLOOR > 0

    def test_clues_scale_the_score(self):
        q = {"type": "mc", "tier": 5, "answer": "Nolan Ryan", "clueCount": 5}
        early = scoring.grade(q, "Ryan", 5.0, clues_taken=0)
        late = scoring.grade(q, "Ryan", 5.0, clues_taken=4)

        assert early["points"] > late["points"]
        assert late["points"] > 0
        assert late["correct"] is True
