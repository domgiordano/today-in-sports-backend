"""
Announcements.

Both rules pinned here are ones every product gets wrong at least once: an
announcement that never ends, and an announcement that interrupts the thing
the person came to do.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from lambdas.common import announcements_dynamo as ann


@pytest.fixture
def table():
    t = MagicMock()
    t.scan.return_value = {"Items": []}
    t.update_item.return_value = {"Attributes": {}}
    with patch.object(ann, "_table", return_value=t):
        yield t


def _row(days_ago_start=1, days_left=7, placements=None):
    now = datetime.now(timezone.utc)
    return {
        "announcementId": "a1",
        "title": "New formats",
        "body": "Maps and clue ladders are live.",
        "severity": "info",
        "placements": placements or ["landing", "results"],
        "startsAt": (now - timedelta(days=days_ago_start)).isoformat(),
        "endsAt": (now + timedelta(days=days_left)).isoformat(),
        "dismissible": True,
        "createdAt": now.isoformat(),
    }


# ------------------------------------------------------------- must end

def test_an_announcement_always_gets_an_end_date(table):
    row = ann.create("Heads up", "Something changed.")
    assert row["endsAt"] > row["startsAt"]


def test_the_run_length_is_capped(table):
    """Nobody should be able to write a banner that runs for a decade."""
    row = ann.create("Forever", "x", run_days=100000)
    start = datetime.fromisoformat(row["startsAt"])
    end = datetime.fromisoformat(row["endsAt"])
    assert (end - start).days <= ann.MAX_RUN_DAYS


def test_an_expired_announcement_stops_appearing(table):
    """Without anybody having to remember to take it down."""
    table.scan.return_value = {"Items": [_row(days_ago_start=30, days_left=-1)]}
    assert ann.active() == []


def test_an_announcement_scheduled_for_later_does_not_appear_yet(table):
    table.scan.return_value = {"Items": [_row(days_ago_start=-5, days_left=10)]}
    assert ann.active() == []


def test_a_running_announcement_appears(table):
    table.scan.return_value = {"Items": [_row()]}
    assert len(ann.active()) == 1


# ------------------------------------------------- must not cover the quiz

def test_the_only_placements_are_landing_and_results():
    """
    Interrupting someone mid-question to mention a new feature is the fastest
    way to make both feel worse, so there is no placement that can.
    """
    assert set(ann.VALID_PLACEMENTS) == {"landing", "results"}


def test_an_invalid_placement_is_dropped(table):
    row = ann.create("x", "y", placements=["landing", "mid-quiz"])
    assert row["placements"] == ["landing"]


def test_an_announcement_with_no_valid_placement_is_refused(table):
    with pytest.raises(ValueError):
        ann.create("x", "y", placements=["mid-quiz"])


def test_filtering_by_placement(table):
    table.scan.return_value = {"Items": [_row(placements=["results"])]}
    assert len(ann.active("results")) == 1
    assert ann.active("landing") == []


# ------------------------------------------------------------- validation

def test_an_announcement_needs_a_title(table):
    with pytest.raises(ValueError):
        ann.create("   ", "body")


def test_an_unknown_severity_is_refused(table):
    with pytest.raises(ValueError):
        ann.create("x", "y", severity="catastrophe")


def test_long_copy_is_truncated_rather_than_rejected(table):
    row = ann.create("t" * 500, "b" * 5000)
    assert len(row["title"]) <= 120
    assert len(row["body"]) <= 600


def test_ending_one_early_does_not_delete_it(table):
    ann.end_now("a1")
    expr = table.update_item.call_args[1]["UpdateExpression"]
    assert "SET endsAt" in expr
    assert "DELETE" not in expr and "REMOVE" not in expr
