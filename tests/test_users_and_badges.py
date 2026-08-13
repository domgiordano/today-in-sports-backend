"""
Streaks and badges.

Both are rule-derived, in the same spirit as question notability: a badge means
exactly one thing and always the same thing, and nothing is awarded on a
judgement call.
"""

from unittest.mock import MagicMock, patch

import pytest

from lambdas.common import badges, users_dynamo as users


# ------------------------------------------------------------------ streaks

class TestStreak:
    def test_a_first_ever_play_starts_at_one(self):
        assert users.next_streak(None, "2026-08-13", 0) == 1

    def test_playing_the_next_day_extends_it(self):
        assert users.next_streak("2026-08-12", "2026-08-13", 4) == 5

    def test_a_second_session_on_one_day_does_not_extend_it(self):
        """A second session is not a second day."""
        assert users.next_streak("2026-08-13", "2026-08-13", 4) == 4

    def test_a_missed_day_resets(self):
        assert users.next_streak("2026-08-11", "2026-08-13", 30) == 1

    def test_it_survives_a_month_boundary(self):
        assert users.next_streak("2026-07-31", "2026-08-01", 3) == 4

    def test_it_survives_a_year_boundary(self):
        assert users.next_streak("2025-12-31", "2026-01-01", 9) == 10

    def test_a_malformed_date_does_not_crash(self):
        assert users.next_streak("not-a-date", "2026-08-13", 5) == 1


# ------------------------------------------------------------------- badges

def _session(count=5, correct=5, seconds=5.0, hints=None, clues=None):
    return {
        "answers": [
            {"index": i, "correct": i < correct, "seconds": str(seconds),
             "credit": "1.0" if i < correct else "0.0"}
            for i in range(count)
        ],
        "hintsUsed": set(hints or ()),
        "cluesTaken": list(clues or ()),
    }


def _questions(count=5, tier=3, qtype="mc"):
    return [{"tier": tier, "type": qtype} for _ in range(count)]


class TestBadges:
    def test_a_first_quiz_is_a_badge(self):
        got = badges.earned(_session(), _questions(), streak=1, play_count=1)
        assert "first-quiz" in got

    def test_a_second_quiz_is_not(self):
        got = badges.earned(_session(), _questions(), streak=2, play_count=2)
        assert "first-quiz" not in got

    def test_five_of_five_is_a_perfect_day(self):
        got = badges.earned(_session(), _questions(), streak=1, play_count=3)
        assert "perfect-day" in got

    def test_four_of_five_is_not(self):
        got = badges.earned(_session(correct=4), _questions(), 1, 3)
        assert "perfect-day" not in got

    def test_a_perfect_day_with_a_hint_is_not_unaided(self):
        assert "unaided" not in badges.earned(
            _session(hints={1}), _questions(), 1, 3)
        assert "unaided" not in badges.earned(
            _session(clues=["2"]), _questions(), 1, 3)
        assert "unaided" in badges.earned(_session(), _questions(), 1, 3)

    def test_answering_slowly_is_not_quick(self):
        assert "quick" not in badges.earned(
            _session(seconds=45.0), _questions(), 1, 3)
        assert "quick" in badges.earned(_session(seconds=4.0), _questions(), 1, 3)

    def test_streak_badges_do_not_stack(self):
        """A month is not also a week; the higher badge stands alone."""
        month = badges.earned(_session(), _questions(), streak=30, play_count=30)
        assert "month-streak" in month and "week-streak" not in month

        week = badges.earned(_session(), _questions(), streak=7, play_count=7)
        assert "week-streak" in week and "month-streak" not in week

    def test_a_deep_cut_needs_both_age_and_no_help(self):
        old = _questions(tier=5)
        assert "deep-cut" in badges.earned(_session(), old, 1, 3)
        # Same question, but the hint was taken on every one of them.
        assert "deep-cut" not in badges.earned(
            _session(hints={0, 1, 2, 3, 4}), old, 1, 3)

    def test_the_map_badge_needs_full_credit(self):
        maps = _questions(qtype="map")
        assert "cartographer" in badges.earned(_session(), maps, 1, 3)

        near_miss = _session()
        for a in near_miss["answers"]:
            a["credit"] = "0.8"
            a["correct"] = False
        assert "cartographer" not in badges.earned(near_miss, maps, 1, 3)

    def test_an_empty_round_earns_nothing(self):
        assert badges.earned({"answers": []}, _questions(), 1, 1) == []

    def test_every_awarded_badge_is_in_the_catalogue(self):
        got = badges.earned(_session(), _questions(tier=5, qtype="map"),
                            streak=30, play_count=1)
        for badge_id in got:
            assert badge_id in badges.BY_ID, badge_id

    def test_describe_returns_full_definitions(self):
        described = badges.describe(["perfect-day", "nonsense"])
        assert len(described) == 1
        assert described[0]["name"] == "Perfect Day"


# --------------------------------------------------------------- recording

class TestRecordPlay:
    @pytest.fixture
    def table(self):
        t = MagicMock()
        t.update_item.return_value = {"Attributes": {"userId": "u1"}}
        with patch.object(users, "_table", return_value=t):
            yield t

    def test_only_genuinely_new_badges_are_returned(self, table):
        with patch.object(users, "get_user",
                          return_value={"badges": ["first-quiz"],
                                        "currentStreak": 1}):
            _, fresh = users.record_play(
                "u1", "2026-08-13", 500, 5, ["first-quiz", "perfect-day"])
        assert fresh == ["perfect-day"]

    def test_replaying_a_day_does_not_inflate_the_play_count(self, table):
        """A second session on one day is still one day."""
        with patch.object(users, "get_user",
                          return_value={"lastPlayedDate": "2026-08-13"}):
            users.record_play("u1", "2026-08-13", 500, 5, [])
        assert "playCount" not in table.update_item.call_args[1]["UpdateExpression"]

    def test_a_new_day_does_increment_it(self, table):
        with patch.object(users, "get_user",
                          return_value={"lastPlayedDate": "2026-08-12"}):
            users.record_play("u1", "2026-08-13", 500, 5, [])
        assert "playCount" in table.update_item.call_args[1]["UpdateExpression"]

    def test_the_longest_streak_never_goes_down(self, table):
        with patch.object(users, "get_user",
                          return_value={"lastPlayedDate": "2026-08-01",
                                        "currentStreak": 20,
                                        "longestStreak": 20}):
            users.record_play("u1", "2026-08-13", 500, 5, [])
        values = table.update_item.call_args[1]["ExpressionAttributeValues"]
        assert values[":streak"] == 1, "a missed fortnight resets the streak"
        assert values[":longest"] == 20, "but the record stands"


class TestRegion:
    """
    Self-declared, and coarse on purpose: a country and optionally a state.
    No city, no county, no coordinates - this exists to filter a leaderboard.
    """

    @pytest.fixture
    def table(self):
        t = MagicMock()
        t.update_item.return_value = {"Attributes": {"userId": "u1"}}
        with patch.object(users, "_table", return_value=t):
            yield t

    def test_a_country_is_normalised_to_two_letters(self, table):
        users.set_region("u1", "  gb  ")
        assert table.update_item.call_args[1][
            "ExpressionAttributeValues"][":c"] == "GB"

    def test_a_country_is_required(self, table):
        with pytest.raises(ValueError):
            users.set_region("u1", "")

    def test_a_subdivision_is_optional_and_removed_when_blank(self, table):
        users.set_region("u1", "US", "")
        assert "REMOVE subdivision" in table.update_item.call_args[1][
            "UpdateExpression"]

    def test_a_subdivision_is_stored_when_given(self, table):
        users.set_region("u1", "US", "New York")
        values = table.update_item.call_args[1]["ExpressionAttributeValues"]
        assert values[":s"] == "New York"

    def test_a_long_subdivision_is_truncated_rather_than_rejected(self, table):
        users.set_region("u1", "US", "x" * 500)
        values = table.update_item.call_args[1]["ExpressionAttributeValues"]
        assert len(values[":s"]) <= users.MAX_REGION_LEN

    def test_a_region_can_be_taken_back_off(self, table):
        users.clear_region("u1")
        assert "REMOVE country" in table.update_item.call_args[1][
            "UpdateExpression"]
