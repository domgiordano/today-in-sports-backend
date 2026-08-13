"""
Shared presentation for the play surface.

These live in `common` — and therefore in the shared layer — rather than in one
handler, because every Lambda is packaged with only its own folder plus that
layer. A handler importing from a sibling handler resolves fine locally and then
fails at cold start in Lambda with `No module named 'lambdas.play_start'`.
"""

from datetime import datetime, timezone


def today_utc():
    """
    The quiz day.

    UTC, deliberately, and stated in the UI. Using the caller's timezone would
    fragment the leaderboard into twenty-four overlapping days and let a player
    see tomorrow's quiz early by changing a setting.
    """
    return datetime.now(timezone.utc).date().isoformat()


def options_for(question):
    """
    The shuffled choices for a multiple-choice question.

    Order is derived from the question id so it is stable across a refresh.
    Reshuffling on every request would let the answer be inferred by reloading
    and watching which option moves.
    """
    if question.get("type") != "mc":
        return None
    return sorted(
        [str(question["answer"])] + list(question.get("distractors") or []),
        key=lambda o: hash((question["questionId"], o)) & 0xFFFF,
    )


def public_question(question, index, total, with_options=False,
                    clues_taken=0):
    """
    Strip a question down to what a player may see.

    Everyone gets the identical daily quiz, so anything left in this payload is
    effectively published to every player at once, and the answer field never
    travels.

    Multiple-choice questions are served as free response by default: the
    options are withheld and offered as a scored hint. That is why they cannot
    ship in this payload — if the client already held them, "did you use the
    hint" would be a question only the client could answer, and the score would
    depend on the honesty of something we do not control.
    """
    show_options = with_options and question.get("type") == "mc"
    qtype = question.get("type")

    # A clue ladder ships only the rungs already paid for. Sending all five and
    # revealing them client-side would put the whole ladder in the payload and
    # make the decay a suggestion.
    clues = None
    if qtype == "clue":
        clues = list(question.get("clues") or [])[:max(1, int(clues_taken) + 1)]

    return {
        "hintAvailable": qtype == "mc" and not show_options,
        # Ordering items are shuffled at generation time and stored that way,
        # so the order here carries no signal about the answer.
        "items": list(question.get("items") or []) if qtype == "ordering" else None,
        # A map question's answer IS a coordinate, so nothing about the venue
        # can travel with the question - not the name, not the country, not a
        # bounding box. All of it is revealed only once the answer is locked in.
        "clues": clues,
        "clueCount": question.get("clueCount") if qtype == "clue" else None,
        "cluesTaken": int(clues_taken) if qtype == "clue" else None,
        "index": index,
        "total": total,
        "questionId": question["questionId"],
        "type": question["type"],
        "tier": question["tier"],
        "prompt": question["prompt"],
        "sport": question["sport"],
        "league": question.get("league"),
        "options": options_for(question) if show_options else None,
        "tolerance": (question.get("tolerance")
                      if question.get("type") == "numeric" else None),
    }
