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

# A closer is a closer. Two clue ladders in one quiz means two questions whose
# prompts both read "Who is this?", which is the most literal repetition the
# game can produce and was shipping daily.
MAX_CLOSERS = 1

# Formats worth having in a quiz when the date can supply one, even though the
# format-settling rule would otherwise pass over them.
#
# The map is the only question that uses the whole screen and the only one that
# is not answered by typing or tapping a list, and it was reaching 26 of 44
# days — enough to be absent for a run of days at a time, which reads as "there
# are no map questions" rather than as variety.
FEATURE_TYPES = ("map",)

# How many distinct interaction formats a five-question quiz should aim for.
#
# Variety of content is the point of the game; variety of *interface* is not.
# Chasing format freshness on every slot produced quizzes where all five
# questions were a different interaction — type a name, pick four of eight, tap
# a map, drag into order, work down a clue ladder — so the player learned a new
# control five times in three minutes and never settled into playing. Three
# formats gives the day a shape without making it monotonous.
TARGET_DISTINCT_FORMATS = 3

# Formats that work as a closer and read badly anywhere else: a clue ladder
# opening a quiz gives away that clues exist before the player has settled in.
CLOSER_TYPES = ("clue",)

# Formats that make a good opening question: quick to read, answerable without
# learning an interaction first. A map or an ordering puzzle at question one
# asks the player to work out the interface before they have answered anything,
# which is the wrong first impression of a game that takes three minutes.
OPENER_TYPES = ("mc", "numeric", "multi")

# How many previous days the opener rotation remembers. Long enough that a
# format cannot come round twice in a week, short enough that a thin stretch of
# calendar is not held to a rotation its inventory cannot serve.
OPENER_MEMORY = 6

# Most questions from one sport in a single quiz.
#
# The bank is 84% baseball, not because baseball matters more but because
# Retrosheet reaches 1871 while the other sources start in the 1990s — roughly
# fifty times the games to draw on. Without a ceiling the deepest archive wins
# every slot on merit and the quiz becomes a baseball quiz.
MAX_PER_SPORT = 2


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


def _best(candidates, chosen_sports, chosen_types=None, prefer_new_sport=True,
          chosen_pairs=None):
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
    chosen_pairs = chosen_pairs or collections.Counter()

    # Formats stop chasing freshness once the quiz has enough of them, and
    # start preferring one already on the board. Without this the mix rule
    # pulled the other way on every slot and guaranteed maximum churn.
    want_new_format = len([t for t, n in chosen_types.items() if n]) < TARGET_DISTINCT_FORMATS

    def key(q):
        fresh_sport = q["sport"] not in chosen_sports
        fresh_type = chosen_types[q.get("type")] == 0
        format_rank = (0 if fresh_type else 1) if want_new_format else (1 if fresh_type else 0)
        # Two questions sharing a sport *and* a format are the same question
        # twice — two German second division scorelines, one after the other.
        # Ranked below everything except raw notability so it is avoided
        # wherever the date has anything else to offer.
        repeat_pair = chosen_pairs[(q["sport"], q.get("type"))] > 0
        return (
            1 if q.get("_borrowed") else 0,
            0 if (prefer_new_sport and fresh_sport) else 1,
            format_rank,
            1 if repeat_pair else 0,
            -(q.get("notabilityScore") or 0),
            q["questionId"],
        )

    return sorted(candidates, key=key)[0]


def _staleness(value, recent):
    """
    How long since `value` last opened a quiz, as a sortable number.

    0 means it opened yesterday and 1 the day before, so a larger number is a
    better opener. Anything not in memory sorts best of all.
    """
    try:
        return recent.index(value)
    except ValueError:
        return len(recent) + 1


def _pick_opener(pool, recent_openers, is_free):
    """
    Choose the question that opens the quiz.

    Slot one used to be whatever tier 1 offered, and tier 1 covers 96 of 366
    dates — so on three days in four the opener fell through to whatever tier 2
    had most of, which is numeric. The result was 72% of published quizzes
    opening with a number and half of them opening with the same sport. The
    ladder is the format, but a ladder with nothing on its bottom rung is not
    worth opening on.

    So the opener is chosen for rotation first — a format and a sport that have
    not opened recently — and tier is only a tiebreak. Everything after it still
    ascends.
    """
    recent_types = [o[0] for o in (recent_openers or [])][:OPENER_MEMORY]
    recent_sports = [o[1] for o in (recent_openers or [])][:OPENER_MEMORY]

    cands = [q for q in pool if q.get("type") in OPENER_TYPES and is_free(q)]
    if not cands:
        # Nothing suitable: rather than force a map or a clue ladder into the
        # opening slot, hand back nothing and let the tier pass fill it.
        return None

    def key(q):
        # Sport before format. Both rotate, but "another baseball question" is
        # the thing a player notices and says out loud, and with 84% of the
        # bank in one sport it is also the one that needs the stronger push.
        return (
            1 if q.get("_borrowed") else 0,
            -_staleness(q.get("sport"), recent_sports),
            -_staleness(q.get("type"), recent_types),
            int(q.get("tier") or 1),
            -(q.get("notabilityScore") or 0),
            q["questionId"],
        )

    return sorted(cands, key=key)[0]


def assemble(quiz_date, questions, used_ids=None, mix=None, recent_openers=None):
    """
    Build one day's quiz.

    `quiz_date` is a UTC yyyy-mm-dd; the calendar key is its MM-DD. `used_ids`
    is every question already used on this calendar date in past years.
    `recent_openers` is [(type, sport), ...] most recent first, from the days
    immediately before this one, so the opening question can rotate rather than
    landing on the same format every morning.
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

    chosen, chosen_ids = [], set()
    # A Counter, not a set: the mix wants to know how many baseball questions
    # are already on the board, not merely that one is.
    chosen_sports = collections.Counter()
    # Two questions from the same event must never share a quiz. They are
    # different questionIds, so id-deduping alone lets them through, and one
    # routinely answers the other: "Babe Ruth was sold to the New York Yankees,
    # how much cash was involved" hands over the answer to "which team did the
    # Red Sox send him to".
    chosen_events = set()

    chosen_types = collections.Counter()
    chosen_pairs = collections.Counter()

    def _take(q):
        chosen.append(q)
        chosen_ids.add(q["questionId"])
        chosen_sports[q["sport"]] += 1
        chosen_types[q.get("type")] += 1
        chosen_pairs[(q["sport"], q.get("type"))] += 1
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

    # Held at two rather than scaled to the pool. An earlier version raised the
    # cap whenever a date looked thin, which pre-emptively surrendered on dates
    # that could in fact have met it — a third of quizzes came out with three
    # or more baseball questions. The two-sweep fill below already relaxes the
    # cap when a date genuinely cannot be filled, so the cap itself should not
    # do that work a second time.
    def _sport_ok(q):
        return chosen_sports[q["sport"]] < MAX_PER_SPORT

    def _pair_ok(q):
        """
        A closer may appear once. Everything else is a preference rather than a
        gate — see `_best`, which ranks a repeated sport-and-format pairing last
        without refusing it, because refusing it outright fought the format
        settling and pushed every quiz back to five different interactions.
        """
        if q.get("type") in CLOSER_TYPES or q.get("type") in FEATURE_TYPES:
            # One each. A feature question is guaranteed a slot precisely
            # because it is a change of pace, and two maps in five is no longer
            # a change of pace — it is the quiz being about maps.
            return chosen_types[q.get("type")] < MAX_CLOSERS
        return True

    # The opener is chosen before the ladder runs, because rotation matters
    # more in slot one than the bottom rung of a ladder that is mostly empty.
    opener = _pick_opener(pool, recent_openers, _free)
    if opener:
        _take(opener)

    # One feature question, claimed before the ladder rather than after it.
    #
    # Placed after the ladder it was refused on 25 of 43 dates that had one to
    # give: maps are 87% baseball, and by then the two-per-sport budget was
    # already spent on ordinary baseball questions. Taking it first means the
    # map *is* one of those two, and the rest of the quiz fills around it.
    if not any(q.get("type") in FEATURE_TYPES for q in chosen):
        feature = [q for q in pool
                   if q.get("type") in FEATURE_TYPES
                   and q["questionId"] not in chosen_ids
                   and _free(q) and _sport_ok(q)]
        pick = _best(feature, chosen_sports, chosen_types, chosen_pairs=chosen_pairs)
        if pick:
            _take(pick)

    # Pass 1 — one question per tier, preferring an unrepresented sport.
    for slot in mix:
        tier = slot["tier"]
        if len(chosen) >= QUIZ_LENGTH:
            break
        cands = [q for q in by_tier.get(tier, [])
                 if q["questionId"] not in chosen_ids and _free(q)
                 and _type_ok(q) and _sport_ok(q) and _pair_ok(q)]
        pick = _best(cands, chosen_sports, chosen_types, chosen_pairs=chosen_pairs)
        if pick:
            _take(pick)

    # Pass 2 — a missing tier is filled from the nearest available one. A quiz
    # whose ladder is 1/2/3/3/5 still ascends; a four-question quiz does not
    # exist.
    if len(chosen) < QUIZ_LENGTH:
        missing = QUIZ_LENGTH - len(chosen)
        filled = 0
        over_type = 0

        over_sport = 0

        # Three sweeps, giving up one constraint at a time in the order they
        # are worth least. Format repetition is the cheapest thing to concede —
        # a second ordering puzzle is duller, not unanswerable — so it yields
        # before sport balance does. Conceding both at once, which is what this
        # did originally, meant a date short of one format quietly bought a
        # third and fourth baseball question it never needed.
        sweeps = (
            (True, True, True),     # everything honoured
            (True, True, False),    # let a sport-and-format pairing repeat
            (False, True, False),   # let the format repeat
            (False, False, False),  # last resort: let one sport dominate
        )
        for honour_type_cap, honour_sport_cap, honour_pair in sweeps:
            while filled < missing:
                cands = [q for q in pool
                         if q["questionId"] not in chosen_ids
                         and _free(q)
                         and (not honour_type_cap or _type_ok(q))
                         and (not honour_sport_cap or _sport_ok(q))
                         and (not honour_pair or _pair_ok(q))]
                if not cands:
                    break

                # Same preference as pass 1, rather than raw tier order. Taking
                # the first thing that fits produced quizzes with two nearly
                # identical prompts in them - back-to-back Eredivisie scorelines
                # - because the filler ignored the sport and format already on
                # the board.
                pick = _best(cands, chosen_sports, chosen_types, chosen_pairs=chosen_pairs)
                if not _type_ok(pick):
                    over_type += 1
                if not _sport_ok(pick):
                    over_sport += 1
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
        if over_sport:
            relaxed.append("sport-cap")
            warnings.append(
                f"{over_sport} slot(s) exceeded the {MAX_PER_SPORT}-per-sport "
                f"cap; this date has too little outside one sport")

    # The shape of the quiz: an on-ramp, a rising middle, a closer.
    #
    # Tier order is still the spine, with two questions pinned around it. The
    # chosen opener stays first whatever its tier — it was picked to be an easy
    # way in and rotated so today does not feel like yesterday — and a closer
    # stays last even if its tier would place it earlier, because a clue ladder
    # at question one gives away that clues exist before the player has settled
    # into answering.
    opener_id = opener["questionId"] if opener else None
    chosen.sort(key=lambda q: (
        0 if q["questionId"] == opener_id else 1,
        q.get("type") in CLOSER_TYPES,
        q["tier"],
        q["questionId"],
    ))

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


def opener_of(questions):
    """
    The (type, sport) pair identifying a quiz's opening question.

    Callers that assemble a run of days keep a short list of these and hand it
    back in, which is what makes the rotation work across a batch rather than
    each date independently reaching for the same strongest question.
    """
    if not questions:
        return None
    first = questions[0]
    return (first.get("type"), first.get("sport"))


def remember_opener(openers, questions):
    """Push this quiz's opener onto a rotation memory, oldest dropped."""
    pair = opener_of(questions)
    if pair:
        openers.insert(0, pair)
        del openers[OPENER_MEMORY:]
    return openers


def assemble_range(dates, questions, used_by_mmdd=None, recent_openers=None):
    """
    Assemble many days, sharing the bank. Used questions accumulate.

    The opener rotation carries across the run, so a week assembled in one go
    varies day to day rather than each date independently reaching for the same
    strongest question. `recent_openers` seeds it with the days immediately
    before `dates`, which is what stops a fresh run repeating the format the
    last run ended on.
    """
    used_by_mmdd = used_by_mmdd or {}
    openers = list(recent_openers or [])
    results = []
    for d in dates:
        mmdd = d[5:]
        result = assemble(d, questions, used_by_mmdd.get(mmdd, set()),
                          recent_openers=openers)
        remember_opener(openers, result.questions)
        results.append(result)
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
