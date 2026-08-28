"""
Emoji on somebody's round.

The rules worth holding: one reaction per person per round, a closed palette,
and a tap that is its own off switch.
"""

import pytest

from lambdas.common import reactions_dynamo as rx


class FakeTable:
    """Enough DynamoDB to exercise the module without one."""

    def __init__(self):
        self.rows = {}

    def get_item(self, Key):
        row = self.rows.get((Key["playId"], Key["reactorId"]))
        return {"Item": row} if row else {}

    def put_item(self, Item):
        self.rows[(Item["playId"], Item["reactorId"])] = Item

    def delete_item(self, Key):
        self.rows.pop((Key["playId"], Key["reactorId"]), None)

    def query(self, **kwargs):
        return {"Items": list(self.rows.values())}


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(rx, "_table", lambda: fake)
    return fake


class TestLeavingOne:
    def test_a_reaction_is_recorded(self, table):
        assert rx.set_reaction("p1", "2026-08-27", "u1", "🔥") == "🔥"
        counts, _ = rx.for_day("2026-08-27")
        assert counts == {"p1": {"🔥": 1}}

    def test_the_same_emoji_again_clears_it(self, table):
        """
        The tap is its own off switch, which is what a button showing its own
        state should do.
        """
        rx.set_reaction("p1", "2026-08-27", "u1", "🔥")
        assert rx.set_reaction("p1", "2026-08-27", "u1", "🔥") is None
        counts, _ = rx.for_day("2026-08-27")
        assert counts == {}

    def test_a_different_emoji_replaces_rather_than_stacks(self, table):
        """
        One person, one reaction. Stacking would make the count a measure of
        how often somebody tapped rather than how many people thought the
        score was worth remarking on.
        """
        rx.set_reaction("p1", "2026-08-27", "u1", "🔥")
        rx.set_reaction("p1", "2026-08-27", "u1", "👏")
        counts, _ = rx.for_day("2026-08-27")
        assert counts == {"p1": {"👏": 1}}

    def test_passing_nothing_clears_it(self, table):
        rx.set_reaction("p1", "2026-08-27", "u1", "🔥")
        assert rx.set_reaction("p1", "2026-08-27", "u1", None) is None
        assert rx.for_day("2026-08-27")[0] == {}

    def test_different_people_each_count(self, table):
        rx.set_reaction("p1", "2026-08-27", "u1", "🔥")
        rx.set_reaction("p1", "2026-08-27", "u2", "🔥")
        rx.set_reaction("p1", "2026-08-27", "u3", "👏")
        counts, _ = rx.for_day("2026-08-27")
        assert counts == {"p1": {"🔥": 2, "👏": 1}}


class TestThePalette:
    def test_an_emoji_outside_the_set_is_refused(self, table):
        """
        Free-form emoji on a leaderboard is a moderation surface, and there is
        no shortage of unpleasant things to leave against somebody's name.
        """
        with pytest.raises(ValueError):
            rx.set_reaction("p1", "2026-08-27", "u1", "🖕")

    def test_arbitrary_text_is_not_a_reaction(self, table):
        with pytest.raises(ValueError):
            rx.set_reaction("p1", "2026-08-27", "u1", "get lost")

    def test_a_refused_reaction_writes_nothing(self, table):
        with pytest.raises(ValueError):
            rx.set_reaction("p1", "2026-08-27", "u1", "🖕")
        assert table.rows == {}


class TestReadingThemBack:
    def test_it_reports_what_each_person_left(self, table):
        """
        The board needs to show a player their own reaction as selected, which
        means knowing which one is theirs and not only how many there were.
        """
        rx.set_reaction("p1", "2026-08-27", "u1", "🔥")
        rx.set_reaction("p2", "2026-08-27", "u1", "💀")
        _, mine = rx.for_day("2026-08-27")
        assert mine["u1"] == {"p1": "🔥", "p2": "💀"}

    def test_a_day_with_no_reactions_is_empty_not_missing(self, table):
        assert rx.for_day("2026-08-27") == ({}, {})

    def test_rows_carry_an_expiry(self, table):
        """They die with the round they are about, so this table never needs sweeping."""
        rx.set_reaction("p1", "2026-08-27", "u1", "🔥")
        assert table.rows[("p1", "u1")]["ttl"] > 0
