"""
Making notability scores comparable across sports.

Each detector sets its own score, and they were never calibrated against one
another. Within a sport that is fine - a no-hitter outranks a blowout. Across
sports it is not: a routine Ligue 1 win scores 82 and Ivan Rodriguez signing as
a free agent scores 60, so the picker preferred a forgotten 5-0 to one of the
most decorated catchers who ever played.

That mattered most in the months the corpus is thinnest. March has 455 events
against April's 1,977, and what fills it is exactly this - "EA Guingamp beat SC
Bastia 5-0" on a date with nothing else. The scores were the only lever the
assembler had for choosing between them, and they pointed the wrong way.

Two adjustments, both derived from data the corpus already holds rather than
from anyone's opinion about which events matter:

  * **Who it happened to.** A transaction involving a player the awards source
    knows about is more memorable than one involving a journeyman, and by how
    much scales with how many awards he won. 420 of 3,091 transactions involve
    an award winner, and they cluster in December, February and March - the
    exact months that needed help.

  * **What competition it was.** openfootball covers second tiers alongside top
    flights, and a Championship result and a Premier League result arrive
    scored identically. The second tier is named in the data, so it can be
    ranked without judging any individual match.

Nothing here invents notability where there was none: a score is only ever
nudged, never assigned, and an event with no player and no league comes out
exactly as it went in.
"""

import re

from lambdas.common.logger import get_logger

log = get_logger(__file__)

# What an award is worth.
#
# Sized against the scores it has to sit between rather than picked for feel.
# `star_free_agent` scores 60 - the lowest in the corpus - because most
# signings genuinely are unremarkable; the detector cannot tell which ones are
# not. That is exactly what an award record can tell it, so the bonus has to be
# big enough to lift the remarkable ones clear of routine game noise.
#
# The landmarks it sits between: a routine top-flight thrashing is 82, a
# no-hitter 92. So a multiple-award winner's move lands at 90 - ahead of any
# league game, behind a no-hitter - and a single-award winner at 75, ahead of a
# second-tier result but not a top-flight one. That last ordering is arguable
# either way, which is why the bonus stops there rather than being tuned until
# one particular pair flipped.
AWARD_BONUS = 15
MAX_AWARD_BONUS = 30

# Second tiers openfootball covers. A 5-0 in the Championship is a real result
# and a worse quiz question than a 5-0 in the Premier League, because far fewer
# people watched it. Named rather than inferred, because "is this a top flight"
# is a fact about a competition and guessing it from the name would put the
# Eredivisie - a top flight - in the wrong bucket.
SECOND_TIER_PATTERNS = (
    r"\bchampionship\b",
    r"\b2\.\s*bundesliga\b",
    r"\bserie b\b",
    r"\bsegunda\b",
    r"\bligue 2\b",
    r"\beerste divisie\b",
)
SECOND_TIER_PENALTY = 10

# The floor. A penalty must never push an event below the point where it stops
# looking like an event at all, or thin dates lose the little they have.
MIN_SCORE = 40

# There is no ceiling, and that is deliberate. Detectors score within about
# 0-99, so a bonus can push an event past 100 - Ichiro's final game comes out
# at 112, being an 82 finale by a two-time award winner. Clamping it back to
# 100 would throw away the ordering that makes this work at exactly the top of
# the list, where it matters most. The score is a sort key, not a percentage.


def _is_second_tier(league):
    text = (league or "").lower()
    return any(re.search(p, text) for p in SECOND_TIER_PATTERNS)


def award_bonus(player, accolades):
    """How much a player's honours add. Zero for anyone the source has never heard of."""
    counts = (accolades or {}).get(player) if player else None
    if not counts:
        return 0
    total = sum(int(v) for v in counts.values())
    return min(total * AWARD_BONUS, MAX_AWARD_BONUS)


def adjusted_score(event, accolades):
    """This event's notability once who and where are taken into account."""
    score = event.get("notabilityScore")
    if score is None:
        return None

    score = int(score)
    facts = event.get("facts") or {}

    # The person the event is about, under whichever key its detector used.
    player = facts.get("player") or facts.get("pitcher")
    score += award_bonus(player, accolades)

    if event.get("sport") == "soccer" and _is_second_tier(event.get("league")):
        score -= SECOND_TIER_PENALTY

    return max(MIN_SCORE, score)


def apply(events, accolades=None):
    """
    Rescore a corpus in place, and report what moved.

    Returns the number of events whose score changed, so a corpus build says
    whether this did anything rather than leaving it to be assumed.
    """
    changed = 0
    for event in events:
        before = event.get("notabilityScore")
        after = adjusted_score(event, accolades)
        if after is not None and after != before:
            event["notabilityScore"] = after
            changed += 1
    log.info(f"prominence: rescored {changed} of {len(events)} events")
    return changed
