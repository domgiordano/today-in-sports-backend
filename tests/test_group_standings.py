"""
The group table.

A group used to report only its own averages — you could see that it scored 640
on average and not who was in it, which is a statistic rather than a
competition.
"""

import pytest

from lambdas.account_groups import handler as h


@pytest.fixture
def group():
    return {
        "groupId": "g1",
        "ownerId": "u1",
        "memberIds": {"u1", "u2", "u3"},
    }


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(h.usernames_dynamo, "current_for", lambda uid: f"{uid}_handle")


def stub_data(monkeypatch, profiles, sessions, reactions=None):
    monkeypatch.setattr(h.users_dynamo, "profiles", lambda ids: profiles)
    monkeypatch.setattr(h.plays_dynamo, "sessions_for", lambda ids, d: sessions)
    monkeypatch.setattr(h.reactions_dynamo, "for_day",
                        lambda d: reactions or ({}, {}))


class TestRanking:
    def test_it_ranks_on_what_accumulates(self, monkeypatch, group):
        """
        Total points, not today's. A daily board resets every morning; the
        season table is the thing worth coming back to.
        """
        stub_data(monkeypatch, {
            "u1": {"userId": "u1", "displayName": "A", "totalPoints": 100},
            "u2": {"userId": "u2", "displayName": "B", "totalPoints": 900},
            "u3": {"userId": "u3", "displayName": "C", "totalPoints": 500},
        }, [])
        rows = h._standings(group, "2026-08-28")
        assert [r["displayName"] for r in rows] == ["B", "C", "A"]
        assert [r["position"] for r in rows] == [1, 2, 3]

    def test_a_tie_is_broken_by_who_has_played_more(self, monkeypatch, group):
        stub_data(monkeypatch, {
            "u1": {"userId": "u1", "displayName": "A", "totalPoints": 500, "playCount": 2},
            "u2": {"userId": "u2", "displayName": "B", "totalPoints": 500, "playCount": 9},
        }, [])
        group["memberIds"] = {"u1", "u2"}
        assert [r["displayName"] for r in h._standings(group, "2026-08-28")] == ["B", "A"]

    def test_the_owner_is_marked(self, monkeypatch, group):
        stub_data(monkeypatch, {"u1": {"userId": "u1", "displayName": "A"}}, [])
        group["memberIds"] = {"u1"}
        assert h._standings(group, "2026-08-28")[0]["isOwner"] is True


class TestToday:
    def test_a_finished_round_shows_its_score(self, monkeypatch, group):
        stub_data(monkeypatch, {"u1": {"userId": "u1", "displayName": "A"}},
                  [{"identity": "u1", "completedAt": "now",
                    "totalPoints": 640, "correctCount": 4}])
        group["memberIds"] = {"u1"}
        row = h._standings(group, "2026-08-28")[0]
        assert (row["todayPoints"], row["todayCorrect"], row["playedToday"]) == (640, 4, True)

    def test_not_having_played_is_none_rather_than_zero(self, monkeypatch, group):
        """
        "Has not played yet" and "played and scored nothing" are different
        things, and a table should not conflate them at nine in the morning.
        """
        stub_data(monkeypatch, {"u1": {"userId": "u1", "displayName": "A"}}, [])
        group["memberIds"] = {"u1"}
        row = h._standings(group, "2026-08-28")[0]
        assert row["todayPoints"] is None
        assert row["playedToday"] is False

    def test_a_round_in_progress_does_not_count_as_played(self, monkeypatch, group):
        """Mid-quiz is not a result, and showing a partial score would leak it."""
        stub_data(monkeypatch, {"u1": {"userId": "u1", "displayName": "A"}},
                  [{"identity": "u1", "totalPoints": 200, "correctCount": 1}])
        group["memberIds"] = {"u1"}
        row = h._standings(group, "2026-08-28")[0]
        assert row["playedToday"] is False
        assert row["todayPoints"] is None


class TestMissingData:
    def test_a_member_with_no_profile_still_appears(self, monkeypatch, group):
        """
        Somebody who joined and has never played is in the group and belongs in
        the table. Dropping them would make the member count disagree with it.
        """
        stub_data(monkeypatch, {}, [])
        rows = h._standings(group, "2026-08-28")
        assert len(rows) == 3
        assert all(r["displayName"] == "Unnamed player" for r in rows)
        assert all(r["totalPoints"] == 0 for r in rows)

    def test_an_empty_group_has_an_empty_table(self, monkeypatch):
        stub_data(monkeypatch, {}, [])
        assert h._standings({"groupId": "g", "memberIds": set()}, "2026-08-28") == []


class TestReactions:
    def test_a_finished_round_carries_its_reactions(self, monkeypatch, group):
        group["memberIds"] = {"u1"}
        stub_data(monkeypatch,
                  {"u1": {"userId": "u1", "displayName": "A"}},
                  [{"identity": "u1", "completedAt": "now", "totalPoints": 640,
                    "correctCount": 4}],
                  ({"u1#2026-08-28": {"\U0001F525": 2}},
                   {"me": {"u1#2026-08-28": "\U0001F525"}}))
        row = h._standings(group, "2026-08-28", viewer_id="me")[0]
        assert row["reactions"] == {"\U0001F525": 2}
        assert row["yourReaction"] == "\U0001F525"

    def test_a_round_still_in_progress_offers_nothing_to_react_to(
            self, monkeypatch, group):
        """
        Nothing to applaud about a quiz somebody is halfway through, and
        offering the buttons would leak that they had started it.
        """
        group["memberIds"] = {"u1"}
        stub_data(monkeypatch,
                  {"u1": {"userId": "u1", "displayName": "A"}},
                  [{"identity": "u1", "totalPoints": 200}],
                  ({"u1#2026-08-28": {"\U0001F525": 2}}, {}))
        row = h._standings(group, "2026-08-28", viewer_id="me")[0]
        assert row["reactions"] == {}
        assert row["yourReaction"] is None

    def test_a_viewer_who_left_none_sees_none_as_theirs(self, monkeypatch, group):
        group["memberIds"] = {"u1"}
        stub_data(monkeypatch,
                  {"u1": {"userId": "u1", "displayName": "A"}},
                  [{"identity": "u1", "completedAt": "now"}],
                  ({"u1#2026-08-28": {"\U0001F44F": 1}},
                   {"someone_else": {"u1#2026-08-28": "\U0001F44F"}}))
        row = h._standings(group, "2026-08-28", viewer_id="me")[0]
        assert row["reactions"] == {"\U0001F44F": 1}
        assert row["yourReaction"] is None
