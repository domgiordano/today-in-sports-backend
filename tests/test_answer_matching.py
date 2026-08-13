"""
Typed-answer matching.

These tests are split deliberately into what must be ACCEPTED and what must be
REJECTED, because the two carry very different costs. A wrongly-rejected
correct answer is the worst thing this product can do to someone; a
wrongly-accepted wrong one costs a few points in a trivia game. The accept list
is therefore generous and the reject list guards only against genuine
collisions between different people.
"""

import pytest

from lambdas.common import answer_matching as am


# ------------------------------------------------------------------ accepted

@pytest.mark.parametrize("typed,expected", [
    ("Nolan Ryan", "Nolan Ryan"),
    ("nolan ryan", "Nolan Ryan"),
    ("  Nolan   Ryan  ", "Nolan Ryan"),
    ("NOLAN RYAN", "Nolan Ryan"),
])
def test_the_same_name_matches_however_it_is_typed(typed, expected):
    assert am.match(typed, expected)[0]


def test_a_surname_alone_is_accepted():
    """How people actually answer. Requiring the full name rejects on formality."""
    assert am.match("Ryan", "Nolan Ryan")[0]
    assert am.match("ryan", "Nolan Ryan")[1] == "surname"


@pytest.mark.parametrize("typed", ["Jose Altuve", "jose altuve", "José Altuve"])
def test_accents_are_optional(typed):
    """People type on keyboards without accents; datasets disagree about them."""
    assert am.match(typed, "José Altuve")[0]


@pytest.mark.parametrize("typed,expected", [
    ("Ken Griffey Jr", "Ken Griffey Jr."),
    ("Ken Griffey", "Ken Griffey Jr."),
    ("Cal Ripken Jr.", "Cal Ripken Jr"),
])
def test_name_suffixes_and_punctuation_do_not_matter(typed, expected):
    assert am.match(typed, expected)[0]


@pytest.mark.parametrize("typed", [
    "New York Yankees", "the New York Yankees", "new york yankees",
])
def test_team_names_tolerate_articles(typed):
    assert am.match(typed, "New York Yankees")[0]


@pytest.mark.parametrize("typed", ["Verlandar", "Verlnader", "Verlander"])
def test_a_typo_in_a_long_surname_is_still_the_right_answer(typed):
    assert am.match(typed, "Justin Verlander")[0]


def test_an_alias_is_accepted():
    assert am.match("Sho-time", "Shohei Ohtani", aliases=["Sho-time"])[0]


def test_word_order_does_not_matter():
    assert am.match("Yankees New York", "New York Yankees")[0]


# ------------------------------------------------------------------ rejected

def test_a_different_person_is_rejected():
    assert not am.match("Justin Verducci", "Justin Verlander")[0]


def test_a_first_name_alone_is_not_enough():
    """
    "Babe" is Ruth, Adams and Herman. A surname identifies; a given name does
    not, so accepting one would mark a genuinely ambiguous answer correct.
    """
    assert not am.match("Babe", "Babe Ruth")[0]


def test_short_answers_must_be_exact():
    """
    At four characters an edit-distance allowance stops discriminating: Rose
    and Ross are one edit apart and are different people.
    """
    assert not am.match("Ross", "Rose")[0]
    assert am.match("Rose", "Rose")[0]


def test_empty_input_is_not_a_match():
    assert not am.match("", "Nolan Ryan")[0]
    assert not am.match("   ", "Nolan Ryan")[0]
    assert not am.match(None, "Nolan Ryan")[0]


def test_a_wholly_different_team_is_rejected():
    assert not am.match("Boston Red Sox", "New York Yankees")[0]


# ------------------------------------------------------------------ mechanics

@pytest.mark.parametrize("a,b,expected", [
    ("kitten", "sitting", 3),
    ("abc", "abc", 0),
    ("abc", "abd", 1),
])
def test_edit_distance(a, b, expected):
    assert am.edit_distance(a, b, cap=5) == expected


def test_edit_distance_gives_up_past_the_cap():
    assert am.edit_distance("aaaa", "bbbbbbbbbb", cap=2) == 3


def test_the_matching_rule_is_reported():
    """
    Knowing which rule accepted an answer is what makes a too-generous rule
    findable rather than a guess.
    """
    assert am.match("Nolan Ryan", "Nolan Ryan")[1] == "exact"
    assert am.match("Ryan", "Nolan Ryan")[1] == "surname"
    assert am.match("Verlandar", "Justin Verlander")[1].startswith("fuzzy")


def test_a_single_typo_is_accepted_not_merely_flagged():
    """One dropped letter in a surname is a slip, and slips are accepted."""
    assert am.match("Grifey", "Ken Griffey")[0]


def test_near_misses_are_flagged_for_review():
    """
    The alias list's raw material: real people typing real names the rules did
    not accept. Two edits out is past the accept threshold but close enough
    that a human should see it.
    """
    assert not am.match("Grfey", "Ken Griffey")[0]
    assert am.near_miss("Grfey", "Ken Griffey")

    assert not am.near_miss("Babe Ruth", "Ken Griffey")
    # An accepted answer is never a near miss.
    assert not am.near_miss("Ryan", "Nolan Ryan")
