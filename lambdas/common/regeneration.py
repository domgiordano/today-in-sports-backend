"""
Which questions the current templates would produce, from the stored events.

One definition, imported by both `regenerate_questions` and
`prune_superseded`, because those two scripts have to agree exactly and
nothing made them. They each carried their own copy of the sport list, the
transaction reason codes and the template calls; a clue-ladder rewrite was
added to one and not the other, and the prune then judged 6,186 rewritten
questions as "not produced by the current templates" — which is to say it could
not see them at all, and retired 23 rows where 6,186 were superseded.

The pair is inherently coupled: regeneration writes the new wording and the
prune removes what the new wording replaced. A row is superseded exactly when
its slot is one of these and its id is not among these. Both halves of that
sentence come from here.
"""

from lambdas.common.templates import mlb_templates as mlb_tpl
from lambdas.common.templates import ordering_templates as ord_tpl
from lambdas.common.templates import transaction_templates as tx_tpl
from lambdas.common.templates import winter_templates as winter_tpl

# Everything that is not baseball. These build their distractor pools from the
# events themselves, so they regenerate without re-fetching a source archive.
WINTER_SPORTS = ("nhl", "nba", "soccer", "nfl", "f1")

# The reason codes the transaction detectors emit, read off the events table
# rather than guessed — a guessed set matched nothing and silently regenerated
# no transaction questions at all.
TRANSACTION_REASONS = {"star_free_agent", "star_trade", "blockbuster_trade",
                       "star_purchase", "landmark_sale", "star_drafted"}

# Baseball templates that need no distractor pool, and so can be rebuilt from
# events alone. The rest draw their wrong answers from that day's *other
# games*, which the events table cannot supply; regenerating those here would
# hand them a thinner pool than they were built with, so they are left to
# `generate_questions.py` and the Retrosheet archive.
MLB_CONTEXT_FREE = ("numeric_blowout_margin",)


def regenerate(events):
    """Every question the templates produce today, from `events`."""
    winter = [e for e in events if e.get("sport") in WINTER_SPORTS]
    transactions = [e for e in events
                    if e.get("sport") == "mlb"
                    and e.get("reason") in TRANSACTION_REASONS]

    out = list(winter_tpl.generate(winter, winter_tpl.build_context(winter)))
    if transactions:
        out += tx_tpl.generate(transactions, tx_tpl.build_context(transactions))

    # Clue ladders build their rungs from the event alone.
    for event in events:
        out.extend(ord_tpl.clue_ladder(event))

    for name in MLB_CONTEXT_FREE:
        template = getattr(mlb_tpl, name)
        for event in (e for e in events if e.get("sport") == "mlb"):
            out.extend(template(event, {}))

    return out


def slots(questions):
    """
    The (event, format) pairs these questions occupy.

    A stored row in one of these slots whose id is not among them is the old
    wording of something that has since been rewritten. A row outside them was
    not regenerated at all and must not be judged.
    """
    return {(q.get("sourceEventId"), q.get("type")) for q in questions}
