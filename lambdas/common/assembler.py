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

Constraint 3 is the one that yields. Thin calendar dates genuinely cannot
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
            "tierLadder": [q["tier"] for q in self.questions],
            "warnings": self.warnings,
            "relaxedConstraints": self.relaxed,
        }

    def __repr__(self):
        return (f"<Quiz {self.quiz_date} n={len(self.questions)} "
                f"relaxed={self.relaxed} warnings={len(self.warnings)}>")


def _eligible(questions, mmdd, used_ids):
    return [q for q in questions
            if q.get("status") == "approved"
            and q.get("mmdd") == mmdd
            and q["questionId"] not in used_ids]


def _best(candidates, chosen_sports, prefer_new_sport=True):
    """
    Pick the strongest candidate, preferring an unrepresented sport.

    Ties break on notability where present, then on questionId so assembly is
    deterministic — the same bank and date must always produce the same quiz.
    """
    if not candidates:
        return None

    def key(q):
        fresh = q["sport"] not in chosen_sports
        return (
            0 if (prefer_new_sport and fresh) else 1,
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

    if not pool:
        return AssemblyResult(quiz_date, [], ["no approved questions for this date"], [])

    by_tier = collections.defaultdict(list)
    for q in pool:
        by_tier[q["tier"]].append(q)

    chosen, chosen_ids, chosen_sports = [], set(), set()

    # Pass 1 — one question per tier, preferring an unrepresented sport.
    for slot in mix:
        tier = slot["tier"]
        cands = [q for q in by_tier.get(tier, []) if q["questionId"] not in chosen_ids]
        pick = _best(cands, chosen_sports)
        if pick:
            chosen.append(pick)
            chosen_ids.add(pick["questionId"])
            chosen_sports.add(pick["sport"])

    # Pass 2 — a missing tier is filled from the nearest available one. A quiz
    # whose ladder is 1/2/3/3/5 still ascends; a four-question quiz does not
    # exist.
    if len(chosen) < QUIZ_LENGTH:
        missing = QUIZ_LENGTH - len(chosen)
        filled = 0
        for q in sorted(pool, key=lambda x: (x["tier"], x["questionId"])):
            if filled == missing:
                break
            if q["questionId"] in chosen_ids:
                continue
            chosen.append(q)
            chosen_ids.add(q["questionId"])
            chosen_sports.add(q["sport"])
            filled += 1
        if filled:
            relaxed.append("tier-ladder")
            warnings.append(
                f"only {QUIZ_LENGTH - filled} distinct tiers available; "
                f"{filled} slot(s) filled from adjacent tiers")

    chosen.sort(key=lambda q: (q["tier"], q["questionId"]))

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
