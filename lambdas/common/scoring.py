"""
Scoring.

One rule outranks everything else here: **a fast wrong answer must never beat a
slow right one.** That single property is what makes the game about knowing
sport rather than about reflexes, and it is why the time component is a bonus
capped as a fraction of the base value rather than a multiplier on it.

The shape:

  * Accuracy is primary. Each question has a base value that rises with tier,
    because a 30-year-old event is genuinely harder than last season's.
  * Time is a decaying bonus with a floor — full inside the grace window,
    halving by the decay window, and never reaching zero. A player who thinks
    for twenty seconds should feel slightly slower, not punished.
  * Numeric questions award partial credit on distance, so "close" is worth
    something. That is the whole point of asking for a number rather than four
    options.

All timing is server-side. The client never reports how long it took, because
the client is not a source of truth about anything.
"""

TIER_BASE = {1: 100, 2: 150, 3: 200, 4: 250, 5: 300}

# Answer inside this and the time bonus is full.
GRACE_SECONDS = 10.0
# By this point the bonus has halved.
DECAY_SECONDS = 30.0
# It never falls below this fraction of the maximum, however long you take.
MIN_BONUS_FRACTION = 0.25
# The bonus can never exceed this share of the question's base value, which is
# what stops speed out-earning correctness.
MAX_BONUS_FRACTION = 0.25

# Beyond this a numeric answer earns nothing, expressed as a multiple of the
# question's tolerance.
NUMERIC_ZERO_AT = 6.0


def base_value(tier):
    return TIER_BASE.get(int(tier or 1), TIER_BASE[1])


def time_bonus(base, seconds):
    """
    Decaying bonus with a floor.

    Deliberately not linear-to-zero: a long pause should cost a little, not
    everything, or the game rewards snap guessing over thinking.
    """
    if seconds is None or seconds < 0:
        seconds = DECAY_SECONDS

    max_bonus = base * MAX_BONUS_FRACTION

    if seconds <= GRACE_SECONDS:
        fraction = 1.0
    else:
        # Halve over the decay window, then flatten at the floor.
        over = seconds - GRACE_SECONDS
        span = max(DECAY_SECONDS - GRACE_SECONDS, 1e-9)
        fraction = max(1.0 - 0.5 * (over / span), MIN_BONUS_FRACTION)

    return round(max_bonus * fraction)


def numeric_credit(answer, guess, tolerance):
    """
    Fraction of base earned by a numeric guess, from distance.

    Exact or inside tolerance is full credit. Beyond that it falls away
    linearly, reaching zero at NUMERIC_ZERO_AT multiples of the tolerance. A
    tolerance of zero still gives a sensible curve by treating one unit as the
    yardstick, so "off by one" is not identical to "off by fifty".
    """
    try:
        answer = float(answer)
        guess = float(guess)
    except (TypeError, ValueError):
        return 0.0

    distance = abs(answer - guess)
    unit = float(tolerance) if tolerance else 1.0

    if distance <= unit:
        return 1.0

    zero_at = unit * NUMERIC_ZERO_AT
    if distance >= zero_at:
        return 0.0
    return max(0.0, 1.0 - (distance - unit) / (zero_at - unit))


def grade(question, submitted, seconds):
    """
    Grade one answer.

    Returns the awarded points, whether it was correct, and the credit fraction
    — the last so the UI can say "close" rather than only "wrong".
    """
    base = base_value(question.get("tier"))
    qtype = question.get("type")

    if qtype == "numeric":
        credit = numeric_credit(
            question.get("numericAnswer", question.get("answer")),
            submitted,
            question.get("tolerance"),
        )
        correct = credit >= 1.0
    else:
        expected = str(question.get("answer", "")).strip().lower()
        got = str(submitted if submitted is not None else "").strip().lower()
        correct = bool(expected) and expected == got
        credit = 1.0 if correct else 0.0

    accuracy_points = round(base * credit)

    # The bonus rides on what was actually earned, so a wrong answer earns no
    # time bonus at all and cannot leapfrog a slower right one.
    bonus = time_bonus(base, seconds) if credit > 0 else 0
    bonus = round(bonus * credit)

    return {
        "points": accuracy_points + bonus,
        "accuracyPoints": accuracy_points,
        "timeBonus": bonus,
        "correct": correct,
        "credit": round(credit, 3),
        "basePoints": base,
        "seconds": round(seconds, 2) if seconds is not None else None,
    }


def max_possible(questions):
    """Perfect score: every answer right, every one inside the grace window."""
    total = 0
    for q in questions:
        base = base_value(q.get("tier"))
        total += base + time_bonus(base, 0)
    return total
