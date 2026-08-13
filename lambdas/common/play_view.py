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


def public_question(question, index, total):
    """
    Strip a question down to what a player may see.

    Everyone gets the identical daily quiz, so anything left in this payload is
    effectively published to every player at once. The correct option ships
    among the choices — that is unavoidable for multiple choice — but nothing
    marks which one it is, and the answer field never travels.

    Option order is derived from the question id so it is stable across a
    refresh. Reshuffling on every request would let the answer be inferred by
    reloading and watching what moves.
    """
    options = None
    if question.get("type") == "mc":
        options = sorted(
            [str(question["answer"])] + list(question.get("distractors") or []),
            key=lambda o: hash((question["questionId"], o)) & 0xFFFF,
        )

    return {
        "index": index,
        "total": total,
        "questionId": question["questionId"],
        "type": question["type"],
        "tier": question["tier"],
        "prompt": question["prompt"],
        "sport": question["sport"],
        "league": question.get("league"),
        "options": options,
        "tolerance": (question.get("tolerance")
                      if question.get("type") == "numeric" else None),
    }
