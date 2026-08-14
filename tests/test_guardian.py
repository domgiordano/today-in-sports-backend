"""
The Guardian source.

This is the first source whose notability is editorial rather than derived, so
the tests are about the two refusals that make it safe to use: never guessing a
date, and never keeping the article.
"""

from datetime import date

import pytest

from lambdas.common.sources import guardian as g


# ------------------------------------------------------------ routine filter

@pytest.mark.parametrize("headline", [
    "Premier League clockwatch – live!",
    "Arsenal v Chelsea: match preview",
    "Six talking points from the weekend",
    "The weekend in pictures",
    "Transfer rumour mill: everyone to everywhere",
])
def test_routine_coverage_is_dropped(headline):
    """A preview and a live blog are published every week. Neither happened."""
    assert g.is_routine(headline)


@pytest.mark.parametrize("headline", [
    "Pacquiao stops De La Hoya in eight rounds",
    "Boruc blunder gifts Hibs victory over Celtic",
    "Ferguson announces retirement",
])
def test_real_events_survive_the_filter(headline):
    assert not g.is_routine(headline)


# ------------------------------------------------------------ date resolution

def test_last_night_means_the_day_before():
    assert g.resolve_event_date(
        "2008-12-07T09:00:00Z", "City won last night") == date(2008, 12, 6)


def test_today_means_the_publication_date():
    assert g.resolve_event_date(
        "2008-12-07T09:00:00Z", "Loeb sealed it this afternoon") == date(2008, 12, 7)


def test_a_named_weekday_is_resolved_backwards():
    # 2008-12-07 was a Sunday; "on Friday" is the 5th.
    assert g.resolve_event_date(
        "2008-12-07T09:00:00Z", "beaten on Friday") == date(2008, 12, 5)


def test_the_same_weekday_is_declined_rather_than_guessed():
    """
    "on Sunday" in a Sunday paper is genuinely ambiguous: print copy tends to
    mean last Sunday, online copy updated through the day routinely means this
    morning.

    This used to assume a week back. Measured over a week of the real archive,
    23% of all resolved candidates landed on that branch — a systematic error
    putting questions on the wrong calendar date, invisible to anyone who did
    not cross-check publication against event. Declining costs a quarter of the
    yield and removes the only failure mode this source cannot survive.
    """
    assert g.resolve_event_date(
        "2008-12-07T09:00:00Z", "the win on Sunday") is None


def test_no_date_reference_returns_none_rather_than_guessing():
    """
    The important branch. Assuming the day before would be right often enough
    to look fine and wrong often enough to put events on days they did not
    happen - and a date-anchored quiz cannot survive that.
    """
    assert g.resolve_event_date("2008-12-07T09:00:00Z", "A report.") is None


@pytest.mark.parametrize("published", ["", None, "garbage", "2008"])
def test_an_unusable_publication_date_returns_none(published):
    assert g.resolve_event_date(published, "last night") is None


# ------------------------------------------------------------------- limits

def test_dates_before_coverage_return_nothing_rather_than_failing():
    """Looking like a bug and being a limit are different things."""
    assert g.fetch_day(date(1985, 6, 1)) == []


def test_the_earliest_year_is_stated():
    assert g.EARLIEST_YEAR == 1999
