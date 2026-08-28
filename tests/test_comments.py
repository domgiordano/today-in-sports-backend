"""
Group comments, and who may read or remove one.

Membership is the whole permission model: these are private groups of at most
fifty people who invited each other, so there is no reporting flow and no
moderation queue. What there must be is a guarantee that nobody outside can
read or write, and that a group cannot be discovered by guessing at ids.
"""

import pytest

from lambdas.common import comments_dynamo as cx
from lambdas.common import group_access
from lambdas.common.errors import NotFoundError, UnauthorizedError


class FakeTable:
    def __init__(self):
        self.rows = {}

    def put_item(self, Item):
        self.rows[(Item["threadId"], Item["postedAtId"])] = Item

    def delete_item(self, Key):
        self.rows.pop((Key["threadId"], Key["postedAtId"]), None)

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None):
        wanted = KeyConditionExpression._values[1]
        items = [v for k, v in self.rows.items() if k[0] == wanted]
        items.sort(key=lambda r: r["postedAtId"], reverse=not ScanIndexForward)
        return {"Items": items[:Limit] if Limit else items}


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(cx, "_table", lambda: fake)
    return fake


class TestPosting:
    def test_a_comment_is_stored_and_read_back(self, table):
        cx.post("g1", "2026-08-28", "u1", "Rough day")
        rows = cx.for_thread("g1", "2026-08-28")
        assert [r["body"] for r in rows] == ["Rough day"]

    def test_a_day_reads_oldest_first(self, table):
        """A conversation reads forwards; a feed reads backwards. This is a
        conversation."""
        for text in ("first", "second", "third"):
            cx.post("g1", "2026-08-28", "u1", text)
        assert [r["body"] for r in cx.for_thread("g1", "2026-08-28")] == [
            "first", "second", "third"]

    def test_days_are_separate_threads(self, table):
        """Today's argument should not be buried under last month's."""
        cx.post("g1", "2026-08-27", "u1", "yesterday")
        cx.post("g1", "2026-08-28", "u1", "today")
        assert len(cx.for_thread("g1", "2026-08-28")) == 1

    def test_groups_are_separate_threads(self, table):
        cx.post("g1", "2026-08-28", "u1", "ours")
        cx.post("g2", "2026-08-28", "u2", "theirs")
        assert [r["body"] for r in cx.for_thread("g1", "2026-08-28")] == ["ours"]

    @pytest.mark.parametrize("body", ["", "   ", None])
    def test_an_empty_comment_is_refused(self, table, body):
        with pytest.raises(ValueError):
            cx.post("g1", "2026-08-28", "u1", body)

    def test_an_over_long_comment_is_refused(self, table):
        with pytest.raises(ValueError):
            cx.post("g1", "2026-08-28", "u1", "x" * (cx.MAX_LENGTH + 1))

    def test_two_comments_in_the_same_instant_both_survive(self, table):
        """
        The sort key carries the id as well as the timestamp, so a collision is
        ordered rather than lost.
        """
        cx.post("g1", "2026-08-28", "u1", "a")
        cx.post("g1", "2026-08-28", "u2", "b")
        assert len(cx.for_thread("g1", "2026-08-28")) == 2

    def test_a_comment_carries_an_expiry(self, table):
        row = cx.post("g1", "2026-08-28", "u1", "hi")
        assert row["ttl"] > 0


class TestDeleting:
    def test_an_author_may_remove_their_own(self, table):
        row = cx.post("g1", "2026-08-28", "u1", "oops")
        assert cx.may_delete(row, "u1", {"ownerId": "u9"}) is True

    def test_the_group_owner_may_remove_anybody(self, table):
        """Somebody has to be able to, and in a group this size it is the
        person who made it."""
        row = cx.post("g1", "2026-08-28", "u1", "oops")
        assert cx.may_delete(row, "u9", {"ownerId": "u9"}) is True

    def test_a_bystander_may_not(self, table):
        row = cx.post("g1", "2026-08-28", "u1", "oops")
        assert cx.may_delete(row, "u2", {"ownerId": "u9"}) is False

    def test_deleting_removes_it(self, table):
        row = cx.post("g1", "2026-08-28", "u1", "oops")
        assert cx.delete("g1", "2026-08-28", row["commentId"]) is True
        assert cx.for_thread("g1", "2026-08-28") == []

    def test_deleting_something_absent_is_not_an_error(self, table):
        assert cx.delete("g1", "2026-08-28", "nope") is False


class TestAccess:
    def _event(self, sub):
        return {"requestContext": {"authorizer": {"sub": sub}}}

    def test_a_signed_out_caller_is_refused(self):
        with pytest.raises(UnauthorizedError):
            group_access.caller({"headers": {}}, "h")

    def test_a_member_gets_the_group(self, monkeypatch):
        monkeypatch.setattr(group_access.groups_dynamo, "get_group",
                            lambda gid: {"groupId": gid, "memberIds": {"u1"}})
        assert group_access.group_for_member("g1", "u1", "h")["groupId"] == "g1"

    def test_a_non_member_is_told_the_group_does_not_exist(self, monkeypatch):
        """
        404 rather than 403, deliberately. A 403 confirms the group is real,
        which turns the endpoint into a way of discovering which ids exist.
        """
        monkeypatch.setattr(group_access.groups_dynamo, "get_group",
                            lambda gid: {"groupId": gid, "memberIds": {"someone"}})
        with pytest.raises(NotFoundError):
            group_access.group_for_member("g1", "outsider", "h")

    def test_a_missing_group_answers_the_same_way(self, monkeypatch):
        monkeypatch.setattr(group_access.groups_dynamo, "get_group",
                            lambda gid: None)
        with pytest.raises(NotFoundError):
            group_access.group_for_member("nope", "u1", "h")

    def test_no_group_id_at_all_answers_the_same_way(self):
        with pytest.raises(NotFoundError):
            group_access.group_for_member("", "u1", "h")


class TestMentions:
    """
    Who a comment addresses, resolved against who is actually in the group.
    """

    HANDLES = {"dom": "u1", "sam": "u2", "alex": "u3"}

    def test_a_handle_resolves_to_the_member(self):
        assert cx.find_mentions("nice one @dom", self.HANDLES) == ["u1"]

    def test_several_are_kept_in_order(self):
        assert cx.find_mentions("@sam @dom both", self.HANDLES) == ["u2", "u1"]

    def test_mentioning_somebody_twice_addresses_them_once(self):
        assert cx.find_mentions("@dom and @dom", self.HANDLES) == ["u1"]

    def test_case_does_not_matter(self):
        assert cx.find_mentions("@DOM", self.HANDLES) == ["u1"]

    def test_a_handle_outside_the_group_resolves_to_nobody(self):
        """
        The rule that keeps a private group private. Otherwise it becomes a way
        of reaching anybody on the app whose handle you can guess.
        """
        assert cx.find_mentions("@stranger hello", self.HANDLES) == []

    def test_an_email_is_not_a_mention(self):
        """
        Somebody pasting an address should not be addressing anybody.

        Tested against a group where the domain *is* a real handle, because
        without the lookbehind this passes for the wrong reason — it resolves
        nothing only until somebody claims the handle "example", and then
        every address quietly starts mentioning them.
        """
        handles = dict(self.HANDLES, example="u9")
        assert cx.find_mentions("mail me at sam@example.com", handles) == []

    def test_a_handle_must_start_a_word(self):
        assert cx.find_mentions("x@dom", self.HANDLES) == []

    def test_punctuation_around_a_handle_is_fine(self):
        assert cx.find_mentions("(@dom), @sam!", self.HANDLES) == ["u1", "u2"]

    def test_a_bare_at_is_not_a_mention(self):
        assert cx.find_mentions("@ @@ @x", self.HANDLES) == []

    def test_it_stops_at_the_cap(self):
        """A comment should not be a way of notifying fifty people at once."""
        handles = {f"u{i}": f"id{i}" for i in range(30)}
        body = " ".join(f"@u{i}" for i in range(30))
        assert len(cx.find_mentions(body, handles)) == cx.MAX_MENTIONS

    def test_no_mentions_stores_no_field(self, table):
        row = cx.post("g1", "2026-08-28", "u1", "nothing to see")
        assert "mentions" not in row

    def test_mentions_are_stored_with_the_comment(self, table):
        row = cx.post("g1", "2026-08-28", "u1", "@dom", mentions=["u1"])
        assert row["mentions"] == ["u1"]
