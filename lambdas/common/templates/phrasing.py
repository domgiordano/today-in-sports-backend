"""
Phrasing variants and competition names, shared by every template module.

A template that emits one sentence skeleton emits it forever. The bank held
25,941 questions across 204 distinct shapes, six of which covered 46% of it, so
by the third day the game read as the same five sentences with the nouns
swapped. That is what "the questions are too predictable" turned out to mean —
not that the facts repeated, but that the sentences did.
"""

import hashlib
import re


def pick(options, *seed):
    """
    One phrasing, chosen deterministically from `seed`.

    Deterministic because a question's identity is a hash of its own prompt: a
    prompt that varied between runs would mint a new questionId every time,
    orphaning the review status and the record of which dates have already used
    it. The same question therefore always reads the same way, while the bank
    as a whole varies.

    Seed on the event and answer rather than the prompt, for the same reason —
    the prompt is the thing being chosen.
    """
    if not options:
        raise ValueError("pick() needs at least one phrasing")
    digest = hashlib.sha1("|".join(str(s) for s in seed).encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


# "French Ligue 1 2021/22" -> "French Ligue 1"
_SEASON_SUFFIX = re.compile(r"\s+(?:19|20)\d{2}\s*[/-]\s*\d{2,4}\s*$")


def competition(name):
    """
    A competition as a person would say it.

    The source labels carry the season — "English Premier League 2022/23" —
    which is how a database names a row and not how anybody speaks. It is also
    redundant in a prompt that already says "on August 27, 2022", and it was
    doing that in 1,331 questions.
    """
    return _SEASON_SUFFIX.sub("", str(name or "")).strip()
