"""
Matching a typed answer against the expected one.

This module exists because recall is a better game than recognition: typing
"Nolan Ryan" is a different act from picking him out of four names, and it is
the one that feels like knowing something. Every multiple-choice question in
the bank can be served as a text box with its options held back as a hint.

**The governing risk is rejecting a correct answer.** A quiz that tells someone
they are wrong when they are right is worse than one that is slightly too easy -
it is the single most enraging thing this product could do, and the player has
no recourse. So every rule here errs toward accepting, and anything that is
close but not accepted is logged rather than silently dropped.

What is deliberately NOT done: no fuzzy matching on short answers, and no
accepting a first name alone. "Ruth" is Babe Ruth, but "Babe" is also Babe
Adams and Babe Herman, and a surname is how people actually answer.
"""

import re
import unicodedata

# Words that carry no identifying information in a sports answer. Stripped from
# both sides so "the New York Yankees" matches "New York Yankees".
NOISE_WORDS = {"the", "a", "an", "of"}

# Suffixes that appear inconsistently across datasets and in what people type.
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

# Below this length an edit-distance allowance stops discriminating: at four
# characters, a distance of one merges "Rose" and "Ross", who are different
# people. Short answers must match exactly after normalisation.
MIN_LENGTH_FOR_FUZZY = 6

# One transposition or typo in a long surname is a slip, not a wrong answer.
MAX_EDIT_DISTANCE = 2


def normalize(text):
    """
    Casefold, strip accents and punctuation, collapse whitespace.

    Accent stripping is what lets "Jose Altuve" match "José Altuve". People type
    on keyboards without accents and the datasets are inconsistent about them,
    so requiring the accent would reject a correct answer on typography.
    """
    if text is None:
        return ""

    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text):
    """Meaningful words, with noise and name suffixes removed."""
    return [t for t in normalize(text).split()
            if t not in NOISE_WORDS and t not in NAME_SUFFIXES]


def surname(text):
    """
    The last meaningful token - how people answer a "who" question.

    Returns None when there is only one token, because then the answer is
    already the whole thing and calling it a surname would let a first name
    through on its own.
    """
    parts = tokens(text)
    return parts[-1] if len(parts) > 1 else None


def edit_distance(a, b, cap=MAX_EDIT_DISTANCE):
    """
    Levenshtein distance, abandoned once it exceeds `cap`.

    The cap is not just an optimisation: without it a long wrong answer and a
    long right one produce a large number nobody uses, and the early exit keeps
    the intent obvious.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ca != cb),
            ))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def _close_enough(got, expected):
    """Edit-distance match, applied only where it discriminates."""
    if len(expected) < MIN_LENGTH_FOR_FUZZY:
        return False
    allowance = 1 if len(expected) < 9 else MAX_EDIT_DISTANCE
    return edit_distance(got, expected, allowance) <= allowance


def match(submitted, expected, aliases=None):
    """
    Compare a typed answer to the expected one.

    Returns (matched, how) where `how` names the rule that accepted it, so a
    near-miss can be reviewed and a too-generous rule can be found and tightened
    rather than guessed at.
    """
    got_raw, want_raw = normalize(submitted), normalize(expected)
    if not got_raw or not want_raw:
        return False, "empty"

    if got_raw == want_raw:
        return True, "exact"

    candidates = [expected] + list(aliases or ())

    for candidate in candidates:
        want = normalize(candidate)
        if not want:
            continue
        if got_raw == want:
            return True, "alias"

        # Surname alone. "Ryan" for "Nolan Ryan" is how people answer, and
        # requiring the full name would reject a correct answer on formality.
        last = surname(candidate)
        if last and got_raw == last:
            return True, "surname"

        # Token-set equality catches reordering and dropped noise words:
        # "Yankees New York" and "New York Yankees" are the same answer.
        if tokens(submitted) and set(tokens(submitted)) == set(tokens(candidate)):
            return True, "tokens"

        if _close_enough(got_raw, want):
            return True, "fuzzy"
        if last and _close_enough(got_raw, last):
            return True, "fuzzy-surname"

    return False, "no-match"


def near_miss(submitted, expected, aliases=None):
    """
    Was a rejected answer close enough to be worth a human look?

    These are the alias list's raw material: a real player typing a real name
    that the rules did not accept is the only reliable signal for what is
    missing. Logged, never auto-accepted.
    """
    matched, _ = match(submitted, expected, aliases)
    if matched:
        return False

    got, want = normalize(submitted), normalize(expected)
    if not got or not want:
        return False

    last = surname(expected) or want
    return edit_distance(got, last, 3) <= 3 or edit_distance(got, want, 3) <= 3
