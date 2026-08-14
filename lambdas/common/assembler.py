"""
Daily quiz assembly.

Picks five approved questions for a calendar date under three constraints, in
descending order of how hard they are to give up:

  1. **No repeats for a returning player.** The whole premise is a date-anchored
     quiz, so August 13 comes round every year. A question already used on a
     date must not reappear there.
  2. **Ascending difficulty.** Tier 1 to tier 5, recent to distant. The ladder is
     the format.
  3. **Sport mix.** Every quiz should span several sports so nobody gets five
     questions about a sport they don't follow.
  4. **Format mix.** No more than two questions of the same type in a day.
     Sport variety alone does not stop a quiz being five multiple-choice
     prompts in a row, and format monotony is what makes a daily game feel the
     same on day two as on day one.

Constraints 3 and 4 are the ones that yield. Thin calendar dates genuinely cannot
satisfy a mix — nothing but baseball happened on many July dates, and nothing at
all happened on some February ones. A quiz with a relaxed mix beats no quiz, so
the assembler degrades and reports rather than failing.

It never invents a question and never lowers the bar on approval status. If a
date cannot be filled from approved inventory, that is a content problem to
surface, not a rule to bend.
"""

import collections

QUIZ_LENGTH = 5

# Preferred slots by tier. Sports are hints, not requirements — see the
# degradation ladder below.
DEFAULT_MIX = [
    {"tier": 1, "prefer": None},
    {"tier": 2, "prefer": None},
    {"tier": 3, "prefer": None},
    {"tier": 4, "prefer": None},
    {"tier": 5, "prefer": None},
]

# How many distinct sports a five-question quiz should ideally span.
TARGET_DISTINCT_SPORTS = 3

# How many questions of one type a five-question quiz may contain, when the
# bank has the variety to support it.
MAX_PER_TYPE = 2

# Formats that work as a closer and read badly anywhere else: a clue ladder
# opening a quiz gives away that clues exist before the player has settled in.
CLOSER_TYPES = ("clue",)


class AssemblyResult:
    def __init__(self, quiz_date, questions, warnings, relaxed):
        self.quiz_date = quiz_date
        self.questions = questions
        self.warnings = warnings
        self.relaxed = relaxed

    @property
    def complete(self):
        return len(self.questions) == QUIZ_LENGTH

    def to_item(self):
        return {
            "quizDate": self.quiz_date,
            "questionIds": [q["questionId"] for q in self.questions],
            "status": "draft",
            "sportMix": dict(collections.Counter(q["sport"] for q in self.questions)),
            "formatMix": dict(collections.Counter(
                q.get("type") for q in self.questions)),
            "tierLadder": [q["tier"] for q in self.questions],
            "warnings": self.warnings,
            "relaxedConstraints": self.relaxed,
        }

    def __repr__(self):
        return (f"<Quiz {self.quiz_date} n={len(self.questions)} "
                f"relaxed={self.relaxed} warnings={len(self.warnings)}>")


def _type_cap(pool):
    """
    The per-format cap this pool can actually satisfy.

    A fixed cap of two is unsatisfiable when the bank holds only two formats:
    two plus two is four, and a quiz is five. Every quiz would then report a
    relaxed constraint, which turns the warning into noise and hides the days
    that genuinely went wrong.

    So the cap scales to the variety available - three when only two formats
    exist, two once there are three or more. It tightens on its own as new
    formats land, without a threshold to remember to change.
    """
    distinct = len({q.get("type") for q in pool}) or 1
    return max(MAX_PER_TYPE, -(-QUIZ_LENGTH // distinct))


# February 29 is the one calendar date the corpus cannot fill on its own.
#
# It has occurred a quarter as often as any other day, so it holds three
# approved questions against a five-question quiz - and `set_status` refuses to
# publish a short one, which means 2028-02-29 would have had no quiz at all
# rather than a thin one.
#
# It borrows from February 28, which is the closest a date-anchored quiz can
# honestly get: those questions are still anchored to a real day next to this
# one, and the alternative is a day with nothing on it. The borrowing is
# recorded as a relaxed constraint so the schedule panel shows it rather than
# quietly presenting the 28th's questions as the 29th's.
LEAP_DAY = "02-29"
LEAP_DAY_FALLBACK = "02-28"


def _eligible(questions, mmdd, used_ids):
    return [q for q in questions
            if q.get("status") == "approved"
            and q.get("mmdd") == mmdd
            and q["questionId"] not in used_ids]


def _best(candidates, chosen_sports, chosen_types=None, prefer_new_sport=True):
    """
    Pick the strongest candidate, preferring an unrepresented sport and format.

    A question actually anchored to this date outranks everything else. Only
    the leap day ever borrows, and on the first run it filled all five slots
    from February 28 while the three real February 29 questions sat unused -
    the borrowed pool was simply larger and won on every other tiebreak. A
    borrowed question is a fallback, not a peer.

    Sport comes next because someone who does not follow a sport cannot answer
    at all, whereas a repeated format is only dull. Ties break on notability
    where present, then on questionId so assembly is deterministic — the same
    bank and date must always produce the same quiz.
    """
    if not candidates:
        return None

    chosen_types = chosen_types or collections.Counter()

    def key(q):
        fresh_sport = q["sport"] not in chosen_sports
        fresh_type = chosen_types[q.get("type")] == 0
        return (
            1 if q.get("_borrowed") else 0,
            0 if (prefer_new_sport and fresh_sport) else 1,
            0 if fresh_type else 1,
            -(q.get("notabilityScore") or 0),
            q["questionId"],
        )

    return sorted(candidates, key=key)[0]


def assemble(quiz_date, questions, used_ids=None, mix=None):
    """
    Build one day's quiz.

    `quiz_date` is a UTC yyyy-mm-dd; the calendar key is its MM-DD. `used_ids`
    is every question already used on this calendar date in past years.
    """
    used_ids = set(used_ids or ())
    mix = mix or DEFAULT_MIX
    mmdd = quiz_date[5:]

    pool = _eligible(questions, mmdd, used_ids)
    warnings = []
    relaxed = []

    if mmdd == LEAP_DAY and len(pool) < QUIZ_LENGTH:
        borrowed = _eligible(questions, LEAP_DAY_FALLBACK, used_ids)
        if borrowed:
            # Marked so `_best` treats them as a fallback rather than a peer;
            # copied so the flag never reaches the stored question.
            borrowed = [dict(q, _borrowed=True) for q in borrowed]
            pool = pool + borrowed
            relaxed.append("leap-day-borrow")
            warnings.append(
                f"february 29 has too few questions of its own; "
                f"{len(borrowed)} borrowed from february 28")

    if not pool:
        return AssemblyResult(quiz_date, [], ["no approved questions for this date"], [])

    by_tier = collections.defaultdict(list)
    for q in pool:
        by_tier[q["tier"]].append(q)

    chosen, chosen_ids, chosen_sports = [], set(), set()
    # Two questions from the same event must never share a quiz. They are
    # different questionIds, so id-deduping alone lets them through, and one
    # routinely answers the other: "Babe Ruth was sold to the New York Yankees,
    # how much cash was involved" hands over the answer to "which team did the
    # Red Sox send him to".
    chosen_events = set()

    chosen_types = collections.Counter()

    def _take(q):
        chosen.append(q)
        chosen_ids.add(q["questionId"])
        chosen_sports.add(q["sport"])
        chosen_types[q.get("type")] += 1
        # A missing id is not a shared id. Recording None would make every
        # question without provenance collide with every other one.
        if q.get("sourceEventId"):
            chosen_events.add(q["sourceEventId"])

    def _free(q):
        event = q.get("sourceEventId")
        return not event or event not in chosen_events

    type_cap = _type_cap(pool)

    def _type_ok(q):
        return chosen_types[q.get("type")] < type_cap

    # Pass 1 — one question per tier, preferring an unrepresented sport.
    for slot in mix:
        tier = slot["tier"]
        cands = [q for q in by_tier.get(tier, [])
                 if q["questionId"] not in chosen_ids and _free(q)
                 and _type_ok(q)]
        pick = _best(cands, chosen_sports, chosen_types)
        if pick:
            _take(pick)

    # Pass 2 — a missing tier is filled from the nearest available one. A quiz
    # whose ladder is 1/2/3/3/5 still ascends; a four-question quiz does not
    # exist.
    if len(chosen) < QUIZ_LENGTH:
        missing = QUIZ_LENGTH - len(chosen)
        filled = 0
        over_type = 0

        # Two sweeps: the first honours the format cap, the second ignores it.
        # A quiz with three multiple-choice questions beats a four-question
        # quiz, so the cap is a preference that yields, not a hard gate — but
        # it only yields once the polite sweep has genuinely run out.
        for honour_type_cap in (True, False):
            while filled < missing:
                cands = [q for q in pool
                         if q["questionId"] not in chosen_ids
                         and _free(q)
                         and (not honour_type_cap or _type_ok(q))]
                if not cands:
                    break

                # Same preference as pass 1, rather than raw tier order. Taking
                # the first thing that fits produced quizzes with two nearly
                # identical prompts in them - back-to-back Eredivisie scorelines
                # - because the filler ignored the sport and format already on
                # the board.
                pick = _best(cands, chosen_sports, chosen_types)
                if not honour_type_cap:
                    over_type += 1
                _take(pick)
                filled += 1

        if filled:
            relaxed.append("tier-ladder")
            warnings.append(
                f"only {QUIZ_LENGTH - filled} distinct tiers available; "
                f"{filled} slot(s) filled from adjacent tiers")
        if over_type:
            relaxed.append("format-mix")
            warnings.append(
                f"{over_type} slot(s) exceeded the {MAX_PER_TYPE}-per-format "
                f"cap; the bank for this date is short on format variety")

    # Tier order is the format, with one exception: a closer stays last even if
    # its tier would place it earlier. A clue ladder opening the quiz gives away
    # that clues exist before the player has settled into answering.
    chosen.sort(key=lambda q: (q.get("type") in CLOSER_TYPES,
                               q["tier"], q["questionId"]))

    if len(chosen) < QUIZ_LENGTH:
        warnings.append(
            f"date is short: {len(chosen)} of {QUIZ_LENGTH} slots filled from "
            f"{len(pool)} approved question(s)")

    distinct = len({q["sport"] for q in chosen})
    if distinct < TARGET_DISTINCT_SPORTS:
        relaxed.append("sport-mix")
        warnings.append(
            f"only {distinct} sport(s) represented; wanted "
            f"{TARGET_DISTINCT_SPORTS}")

    return AssemblyResult(quiz_date, chosen, warnings, relaxed)


def assemble_range(dates, questions, used_by_mmdd=None):
    """Assemble many days, sharing the bank. Used questions accumulate."""
    used_by_mmdd = used_by_mmdd or {}
    results = []
    for d in dates:
        mmdd = d[5:]
        results.append(assemble(d, questions, used_by_mmdd.get(mmdd, set())))
    return results


def coverage_report(questions):
    """Approved questions per calendar date, and how many dates fall short."""
    approved = [q for q in questions if q.get("status") == "approved"]
    per_date = collections.Counter(q["mmdd"] for q in approved)
    return {
        "approvedTotal": len(approved),
        "datesCovered": len(per_date),
        "datesUnder5": sum(1 for d, n in per_date.items() if n < QUIZ_LENGTH),
        "datesEmpty": 366 - len(per_date),
        "perDate": dict(per_date),
    }
