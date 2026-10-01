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

import os

from lambdas.common.sources import nba_franchises
from lambdas.common.templates import history_templates as history_tpl
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

# Score-arithmetic questions ("how many goals did they put past them?"),
# replaced by the "which team did it" questions in history_templates. No
# template produces these any more, so they occupy no regenerated slot and
# would never be judged superseded on that rule alone - this names them.
RETIRED_NUMERIC = {
    "mlb": {"blowout", "slugfest", "postseason_shutout",
            "world_series_game", "world_series_game7"},
    "nba": {"nba_blowout", "nba_playoff_blowout", "nba_shootout", "nba_low_score"},
    "nfl": {"regular_season_blowout", "regular_season_shootout", "rock_fight",
            "super_bowl"},
    "soccer": {"soccer_big_win", "soccer_goal_fest"},
}


def retired(question):
    return (question.get("type") == "numeric"
            and question.get("sourceReason") in RETIRED_NUMERIC.get(question.get("sport"), ()))


def load_franchises():
    """The NBA name history from the local cache both scripts share."""
    return nba_franchises.load(os.environ.get("TIS_CACHE", os.path.expanduser("~/.cache/tis")))


def regenerate(events, franchises=None):
    """
    Every question the templates produce today, from `events`.

    `franchises` is the NBA name history. Without it no basketball question
    can name a club, so neither the winter nor the history templates produce
    one.
    """
    winter = [e for e in events if e.get("sport") in WINTER_SPORTS]
    transactions = [e for e in events
                    if e.get("sport") == "mlb"
                    and e.get("reason") in TRANSACTION_REASONS]

    out = list(winter_tpl.generate(winter, winter_tpl.build_context(winter, franchises)))
    out += history_tpl.generate(events, history_tpl.build_context(events, franchises))
    if transactions:
        out += tx_tpl.generate(transactions, tx_tpl.build_context(transactions))

    # Clue ladders build their rungs from the event alone.
    for event in events:
        out.extend(ord_tpl.clue_ladder(event))

    return out


def slots(questions):
    """
    The (event, format) pairs these questions occupy.

    A stored row in one of these slots whose id is not among them is the old
    wording of something that has since been rewritten. A row outside them was
    not regenerated at all and must not be judged.
    """
    return {(q.get("sourceEventId"), q.get("type")) for q in questions}
