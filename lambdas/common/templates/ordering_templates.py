"""
Ordering and clue-ladder questions.

Both formats are assembled from events already in the corpus and need no new
data at all - which is the argument for building them before anything that does.

Ordering asks a genuinely different thing from multiple choice. "Which of these
came first" cannot be guessed at 25%, it rewards partial knowledge through
per-pair credit, and it is the format that most changes how a day feels.

The clue ladder is the retention mechanic: five facts about one event, revealed
on demand, worth less each time. Every clue is a field the corpus already holds,
so this is assembly rather than authoring, and the decay *is* the credit
fraction - there is no separate grading.
"""

import hashlib

from lambdas.common.templates.mlb_templates import pretty_date, tier_for

__all__ = ["generate", "validate", "build_context"]

# Four, not five. Five items is fiddly on a phone and the extra difficulty is
# marginal, while the failure mode - mis-dragging on a small screen - is not.
ORDER_ITEMS = 4

# A clue ladder needs enough rungs for the decay to mean something.
MIN_CLUES = 3
MAX_CLUES = 5


def _qid(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _stable_shuffle(items, seed):
    """Deterministic ordering, so a regenerated corpus is byte-identical."""
    return sorted(
        items,
        key=lambda i: hashlib.sha1(f"{seed}{i}".encode()).hexdigest())


def _q(event, qtype, prompt, answer, **kw):
    q = {
        "questionId": _qid(event["gameId"], qtype, prompt),
        "type": qtype,
        "tier": tier_for(event["year"]),
        "prompt": prompt,
        "answer": answer,
        "sport": event["sport"],
        "league": event["league"],
        "isNegroLeagues": event.get("isNegroLeagues", False),
        "mmdd": event["mmdd"],
        "year": event["year"],
        "sourceEventId": event["gameId"],
        "sourceReason": event["reason"],
        "sourceName": event["sourceName"],
        "sourceDatasetRef": event["sourceDatasetRef"],
        "status": "draft",
    }
    q.update(kw)
    return q


# ------------------------------------------------------------------ ordering

def chronological(events, ctx=None):
    """
    Put four events from this calendar date into the order they happened.

    Works across every sport and needs only a year, which makes it the one
    ordering question that can be built for any date in the corpus. The events
    must be from distinct years or the question has no single right answer.
    """
    by_year = {}
    for e in events:
        # First event of each year wins, deterministically.
        if e["year"] not in by_year or e["gameId"] < by_year[e["year"]]["gameId"]:
            by_year[e["year"]] = e

    picked = sorted(by_year.values(), key=lambda e: e["year"])
    if len(picked) < ORDER_ITEMS:
        return []

    # Spread across the whole span rather than taking four adjacent years:
    # 1974/1975/1976/1977 is a memory test, 1918/1954/1986/2016 is a question.
    step = len(picked) / ORDER_ITEMS
    chosen = [picked[int(i * step)] for i in range(ORDER_ITEMS)]

    labels = [_label(e) for e in chosen]
    if len(set(labels)) != len(labels):
        return []
    if any(not l for l in labels):
        return []

    anchor = chosen[-1]
    prompt = ("Put these four moments in the order they happened, "
              "earliest first.")

    return [_q(anchor, "ordering", prompt, labels,
               items=_stable_shuffle(labels, anchor["gameId"]),
               # Every item carries its own provenance, because a reviewer has
               # to be able to check four facts, not one.
               itemSources=[{"label": _label(e), "year": e["year"],
                             "ref": e["sourceDatasetRef"]} for e in chosen])]


def _label(event):
    """
    How one event reads as a draggable item.

    The year must not appear: it is the answer. This is the whole reason
    ordering questions cannot reuse the existing event titles, several of which
    lead with the date.
    """
    title = (event.get("title") or "").strip()
    if not title:
        return ""
    year = str(event["year"])
    if year in title:
        return ""
    if pretty_date(event["gameDate"]) in title:
        return ""
    return title


# --------------------------------------------------------------- clue ladder

# Ordered from least to most revealing: era, then what happened, then the
# numbers, then the clubs, then the exact date.
#
# The middle rungs are the ones that matter. A ladder built only from era, sport
# and date - which is what the first version of this produced - asks "who did
# something in baseball in 1903", which is not a question. Every clue below
# except the first two comes from a fact the corpus actually holds about the
# person, and a ladder without at least two of them is rejected outright.
CLUE_BUILDERS = [
    lambda e, f: (f"This happened in {_decade(e['year'])}."
                  if e.get("year") else None),
    lambda e, f: (f"The sport was {_sport_label(e)}." if e.get("sport") else None),
    lambda e, f: _what_happened(e, f),
    lambda e, f: _by_the_numbers(e, f),
    lambda e, f: (f"One of the clubs involved was the {_club(f)}."
                  if _club(f) else None),
    lambda e, f: (f"The other club was the {f['fromTeam']}."
                  if f.get("fromTeam") else None),
    lambda e, f: (f"It happened on {pretty_date(e['gameDate'])}."
                  if e.get("gameDate") else None),
]

# Clues that identify nothing on their own. A ladder made only of these is
# unanswerable however many rungs it has.
GENERIC_PREFIXES = ("This happened in", "The sport was", "It happened on")


def _what_happened(event, facts):
    """The achievement itself, which is the point of the question."""
    reason = event.get("reason")

    if reason == "pitcher_win_milestone":
        wins = facts.get("careerWins")
        return f"He reached a career milestone: his {wins}th win." if wins else None
    if reason == "player_debut":
        return "This was his first appearance in the major leagues."
    if reason == "player_finale":
        return "This was the last game he ever played."
    if reason in ("star_trade", "blockbuster_trade"):
        count = facts.get("playerCount") or 0
        if count > 2:
            return f"He was the headline name in a {count}-player trade."
        return "He was traded."
    if reason == "star_purchase":
        return "He was sold outright, for cash."
    if reason == "star_free_agent":
        return "He signed as a free agent."
    if reason == "star_drafted":
        return "He was selected in a draft."
    return None


def _club(facts):
    """
    The club, wherever this kind of event happens to record it.

    Milestone events store it as `team`; transactions as `toTeam`. Reading only
    one of them dropped the single most useful clue from every milestone ladder.
    """
    return facts.get("toTeam") or facts.get("team")


def _by_the_numbers(event, facts):
    """
    A number that narrows it down, and is not the number already given.

    Career wins deliberately absent: `_what_happened` already states the
    milestone, and emitting both produced consecutive rungs reading "his 100th
    win" and "he won 100 games in his career" - two clues for the price of one
    fact, which makes the ladder shorter than it looks.
    """
    if facts.get("careerStarts") and facts.get("spanYears"):
        return (f"His career ran to {facts['careerStarts']} starts "
                f"across {facts['spanYears']} seasons.")
    if facts.get("amount"):
        return f"The deal was worth ${facts['amount']:,}."
    if facts.get("playerCount", 0) > 2:
        return f"{facts['playerCount']} players changed hands in total."
    return None


def _decade(year):
    return f"the {int(year) // 10 * 10}s"


def _sport_label(event):
    return {"mlb": "baseball", "nhl": "ice hockey", "nfl": "American football",
            "f1": "Formula One", "nba": "basketball",
            "soccer": "football"}.get(event.get("sport"), event.get("sport"))


def clue_ladder(event, ctx=None):
    """
    One event, revealed a fact at a time, worth less with each clue.

    Only built where the corpus holds a person to ask about: a ladder ending in
    "which team was it" is a worse multiple-choice question, not a better
    format.
    """
    facts = event.get("facts") or {}
    answer = facts.get("player")
    if not answer:
        return []

    clues = []
    for build in CLUE_BUILDERS:
        try:
            clue = build(event, facts)
        except Exception:  # noqa: BLE001 - a missing field is not a crash
            clue = None
        if clue and clue not in clues:
            clues.append(clue)

    # A ladder needs real information in it, not just era and sport. Two
    # identifying clues is the floor: below that the question is "who did
    # something in baseball in 1903", which nobody can answer and which the
    # first version of this generator produced 5,686 of.
    identifying = [c for c in clues
                   if not c.startswith(GENERIC_PREFIXES)]
    if len(identifying) < 2:
        return []

    if len(clues) < MIN_CLUES:
        return []

    # Trim from the middle, never the end. There are more builders than rungs,
    # so a plain truncation dropped the exact date — the most revealing clue —
    # and left the ladder finishing on something vaguer than the rung before it.
    if len(clues) > MAX_CLUES:
        clues = clues[:MAX_CLUES - 1] + [clues[-1]]

    # The answer must not be sitting in its own clues.
    if any(answer.lower() in c.lower() for c in clues):
        return []

    prompt = "Who is this? Take a clue at a time — each one costs you points."

    return [_q(event, "clue", prompt, answer,
               clues=clues, clueCount=len(clues))]


# ------------------------------------------------------------------ assembly

def validate(q):
    """Format-specific checks, on top of the shared provenance rules."""
    problems = []
    if not q.get("sourceDatasetRef"):
        problems.append("missing sourceDatasetRef")
    if not q.get("sourceName"):
        problems.append("missing sourceName")
    if not (1 <= q.get("tier", 0) <= 5):
        problems.append("bad tier")

    if q["type"] == "ordering":
        answer, items = q.get("answer") or [], q.get("items") or []
        if len(answer) != ORDER_ITEMS:
            problems.append(f"{len(answer)} items, expected {ORDER_ITEMS}")
        if sorted(map(str, items)) != sorted(map(str, answer)):
            problems.append("items are not a permutation of the answer")
        if len(set(map(str, answer))) != len(answer):
            problems.append("duplicate items")
        if any(not str(a).strip() for a in answer):
            problems.append("blank item")

    if q["type"] == "clue":
        clues = q.get("clues") or []
        if len(clues) < MIN_CLUES:
            problems.append(f"only {len(clues)} clues")
        if not q.get("answer"):
            problems.append("missing answer")
        if q.get("clueCount") != len(clues):
            problems.append("clueCount does not match the clues")

    return problems


def build_context(events):
    return {}


def generate(events, ctx=None):
    """
    Ordering questions per calendar date, clue ladders per event.

    Ordering is deliberately built per date rather than per event: the whole
    question is a comparison between events that share a calendar day.
    """
    out = []

    by_date = {}
    for e in events:
        by_date.setdefault(e["mmdd"], []).append(e)

    for mmdd, day_events in sorted(by_date.items()):
        for q in chronological(day_events):
            if not validate(q):
                out.append(q)

    for event in events:
        for q in clue_ladder(event):
            if not validate(q):
                out.append(q)

    return out
