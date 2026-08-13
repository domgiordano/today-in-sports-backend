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

from lambdas.common import answer_matching

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

# What a right answer is worth after taking the multiple-choice hint.
#
# High enough that taking it is not a wasted question - a player who needs the
# options should still want to answer - and low enough that recalling the name
# unaided is clearly the better play. Recognition is an easier task than recall,
# and the scoring should say so.
HINT_CREDIT = 0.6

# The least a clue-ladder question can be worth once every clue has been taken.
# Not zero: a player who works it out from the last clue still knew something,
# and a question that becomes worthless is one people stop finishing.
CLUE_FLOOR = 0.25

# Map questions. Full credit for landing within this many kilometres of the
# venue - roughly "the right city" - falling away to nothing at the far bound.
# A continental-scale miss should be worth zero, not a consolation.
MAP_FULL_CREDIT_KM = 50.0
MAP_ZERO_AT_KM = 2000.0


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


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in kilometres."""
    import math

    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def map_credit(answer, submitted):
    """
    Fraction earned by a tap on the map, from great-circle distance.

    The same closest-guess mechanic as a numeric question, in two dimensions:
    the right city is full marks, and being on the wrong continent is worth
    nothing rather than a participation fraction.
    """
    try:
        alat, alng = float(answer["lat"]), float(answer["lng"])
        glat, glng = float(submitted["lat"]), float(submitted["lng"])
    except (TypeError, ValueError, KeyError, IndexError):
        return 0.0

    if not (-90 <= glat <= 90 and -180 <= glng <= 180):
        return 0.0

    distance = haversine_km(alat, alng, glat, glng)
    if distance <= MAP_FULL_CREDIT_KM:
        return 1.0
    if distance >= MAP_ZERO_AT_KM:
        return 0.0
    span = MAP_ZERO_AT_KM - MAP_FULL_CREDIT_KM
    return max(0.0, 1.0 - (distance - MAP_FULL_CREDIT_KM) / span)


def multi_credit(answer, submitted):
    """
    Fraction earned by a pick-four-of-eight, with wrong picks subtracting.

    Rewarding hits alone would make selecting all eight a perfect score, which
    turns the format into a button press. Each wrong pick cancels a right one,
    so a shotgun answer earns nothing and a genuinely partial answer still
    scores.

    Selecting more than the question asked for is not a smarter strategy - it
    is the strategy this arithmetic exists to close off.
    """
    if not isinstance(answer, (list, tuple)) or not answer:
        return 0.0
    if not isinstance(submitted, (list, tuple)):
        return 0.0

    correct = {str(a) for a in answer}
    picked = {str(s) for s in submitted}

    hits = len(picked & correct)
    misses = len(picked - correct)

    return max(0.0, (hits - misses) / len(correct))


def ordering_credit(answer, submitted):
    """
    Fraction of correctly ordered pairs — Kendall tau, normalised to 0..1.

    Deliberately not all-or-nothing. Getting four items into order with one
    adjacent pair swapped is five of six pairs right, and scoring that as zero
    would make the format feel arbitrary rather than hard. It is also legible:
    "you had one pair the wrong way round" is a thing a player can understand
    without being shown the working.
    """
    if not isinstance(answer, (list, tuple)) or len(answer) < 2:
        return 0.0
    if not isinstance(submitted, (list, tuple)):
        return 0.0

    # An answer that is not a permutation of the items is not a partial answer.
    if sorted(map(str, submitted)) != sorted(map(str, answer)):
        return 0.0

    position = {str(item): i for i, item in enumerate(answer)}
    got = [position[str(item)] for item in submitted]

    total = concordant = 0
    for i in range(len(got)):
        for j in range(i + 1, len(got)):
            total += 1
            if got[i] < got[j]:
                concordant += 1

    return concordant / total if total else 0.0


def clue_credit(clues_taken, clue_count):
    """
    What a right answer is worth after taking `clues_taken` hints.

    The decay is the credit, so the clue ladder needs no grading of its own:
    answering on the first clue is full value and every further clue costs an
    equal share, down to a floor that keeps the last clue worth playing for.
    """
    if not clue_count or clues_taken <= 0:
        return 1.0
    taken = min(int(clues_taken), int(clue_count) - 1)
    step = (1.0 - CLUE_FLOOR) / max(int(clue_count) - 1, 1)
    return max(CLUE_FLOOR, 1.0 - step * taken)


def grade(question, submitted, seconds, hint_used=False, clues_taken=0):
    """
    Grade one answer.

    Returns the awarded points, whether it was correct, and the credit fraction
    — the last so the UI can say "close" rather than only "wrong".

    `hint_used` means the player asked for the multiple-choice options on a
    question served as free response. It scales credit rather than gating the
    answer: they still got it right, and the score records that they needed
    help doing it.
    """
    base = base_value(question.get("tier"))
    qtype = question.get("type")

    if qtype == "multi":
        credit = multi_credit(question.get("answer"), submitted)
        correct = credit >= 1.0
    elif qtype == "map":
        credit = map_credit(question.get("answer"), submitted)
        correct = credit >= 1.0
    elif qtype == "ordering":
        credit = ordering_credit(question.get("answer"), submitted)
        # Full marks means every pair in the right place, not merely a good try.
        correct = credit >= 1.0
    elif qtype == "numeric":
        credit = numeric_credit(
            question.get("numericAnswer", question.get("answer")),
            submitted,
            question.get("tolerance"),
        )
        correct = credit >= 1.0
    else:
        # Typed answers are matched generously — see answer_matching for why
        # rejecting a correct answer is the failure that matters here.
        correct, _ = answer_matching.match(
            submitted, question.get("answer"), question.get("answerAliases"))
        credit = 1.0 if correct else 0.0

    if hint_used:
        credit *= HINT_CREDIT

    if clues_taken:
        credit *= clue_credit(clues_taken, question.get("clueCount"))

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
        "hintUsed": bool(hint_used),
        "cluesTaken": int(clues_taken or 0),
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
