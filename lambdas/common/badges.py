"""
Badges, derived by rule from a finished round.

Same principle as question notability: every badge below is arithmetic over
values the system already has. Nothing is awarded on a judgement call, so a
badge means exactly one thing and always the same thing.

Deliberately not here: anything that needs a new event stream, anything that
rewards playing at a particular time of day, and anything a player cannot work
out how to earn from the name alone.

Anonymous players earn no badges. Not as a punishment - they can play, score
and appear on the day's board - but because a badge that a cleared browser
deletes is worse than no badge, and one that twenty devices can farm is not an
achievement. Badges are the reason to sign up rather than a thing to police.
"""

# Definitions live in one list so the UI can render them from the same source
# that awards them, rather than duplicating the copy and drifting.
CATALOGUE = [
    {
        "id": "first-quiz",
        "name": "First Day",
        "description": "Played your first quiz.",
    },
    {
        "id": "perfect-day",
        "name": "Perfect Day",
        "description": "All five right.",
    },
    {
        "id": "unaided",
        "name": "No Help Needed",
        "description": "All five right without taking a single hint or clue.",
    },
    {
        "id": "quick",
        "name": "Off the Top of Your Head",
        "description": "Answered all five inside the time bonus window.",
    },
    {
        "id": "week-streak",
        "name": "Seven Straight",
        "description": "Played seven days in a row.",
    },
    {
        "id": "month-streak",
        "name": "A Month of Days",
        "description": "Played thirty days in a row.",
    },
    {
        "id": "deep-cut",
        "name": "Deep Cut",
        "description": "Got a thirty-year-old question right with no help.",
    },
    {
        "id": "cartographer",
        "name": "Cartographer",
        "description": "Landed a map guess within fifty kilometres.",
    },
]

BY_ID = {b["id"]: b for b in CATALOGUE}

WEEK = 7
MONTH = 30
# The grace window in scoring.py: answer inside it and the time bonus is full.
QUICK_SECONDS = 10.0
# Tier 5 is thirty years and older.
DEEP_TIER = 5


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def earned(session, questions, streak, play_count):
    """
    Every badge this finished round earns.

    `session` is the stored play session, `questions` the five questions in the
    order they were served, `streak` the run length after today's play and
    `play_count` the lifetime total including today.
    """
    answers = list(session.get("answers") or [])
    total = len(questions) or len(answers)
    if not answers:
        return []

    by_index = {int(a.get("index", -1)): a for a in answers}
    correct = [a for a in answers if a.get("correct")]

    out = []

    if play_count <= 1:
        out.append("first-quiz")

    perfect = total > 0 and len(correct) == total
    if perfect:
        out.append("perfect-day")

    hints = set(session.get("hintsUsed") or set())
    clues = list(session.get("cluesTaken") or [])
    if perfect and not hints and not clues:
        out.append("unaided")

    if total and all(_num(a.get("seconds"), 999) <= QUICK_SECONDS
                     for a in answers):
        out.append("quick")

    if streak >= MONTH:
        out.append("month-streak")
    elif streak >= WEEK:
        out.append("week-streak")

    for index, question in enumerate(questions):
        answer = by_index.get(index)
        if not answer or not answer.get("correct"):
            continue

        unaided = index not in hints and str(index) not in [str(c) for c in clues]
        if int(question.get("tier") or 0) >= DEEP_TIER and unaided:
            if "deep-cut" not in out:
                out.append("deep-cut")

        if question.get("type") == "map" and _num(answer.get("credit")) >= 1.0:
            if "cartographer" not in out:
                out.append("cartographer")

    return out


def describe(badge_ids):
    """Full definitions, in catalogue order, for the ids given."""
    wanted = set(badge_ids or ())
    return [b for b in CATALOGUE if b["id"] in wanted]
