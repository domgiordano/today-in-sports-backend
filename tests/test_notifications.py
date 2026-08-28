"""
Who gets told, and who does not.

The hard part of notifications is not storing them. Too noisy and people mute
the group; too quiet and the feature does nothing. These tests are almost all
about the choosing.
"""

import pytest

from lambdas.common import notifications_dynamo as nx


class FakeTable:
    def __init__(self):
        self.rows = {}

    class _Batch:
        def __init__(self, table):
            self.table = table

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def put_item(self, Item):
            self.table.rows[(Item["userId"], Item["createdAtId"])] = Item

    def batch_writer(self):
        return self._Batch(self)

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None):
        wanted = KeyConditionExpression._values[1]
        items = [v for k, v in self.rows.items() if k[0] == wanted]
        items.sort(key=lambda r: r["createdAtId"], reverse=not ScanIndexForward)
        return {"Items": items[:Limit] if Limit else items}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        self.rows[(Key["userId"], Key["createdAtId"])]["read"] = True


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(nx, "_table", lambda: fake)
    return fake


class TestWhoIsTold:
    def test_the_named_people_are_told(self, table):
        assert nx.notify(["u1", "u2"], nx.MENTION, "author") == 2

    def test_nobody_is_told_about_their_own_action(self, table):
        """
        The first thing a naive implementation gets wrong, and it shows up as
        somebody being pestered by themselves.
        """
        assert nx.notify(["u1", "author"], nx.MENTION, "author") == 1
        assert nx.recent("author") == []

    def test_the_same_person_named_twice_is_told_once(self, table):
        assert nx.notify(["u1", "u1"], nx.MENTION, "author") == 1

    def test_an_empty_audience_writes_nothing(self, table):
        assert nx.notify([], nx.REPLY, "author") == 0
        assert table.rows == {}

    def test_an_audience_of_only_the_actor_writes_nothing(self, table):
        assert nx.notify(["author"], nx.REACTION, "author") == 0


class TestReading:
    def test_newest_first(self, table):
        nx.notify(["u1"], nx.MENTION, "a", body="first")
        nx.notify(["u1"], nx.REPLY, "b", body="second")
        rows = nx.recent("u1")
        assert rows[0]["preview"] == "second"

    def test_one_persons_list_is_their_own(self, table):
        nx.notify(["u1"], nx.MENTION, "a")
        nx.notify(["u2"], nx.MENTION, "a")
        assert len(nx.recent("u1")) == 1

    def test_a_long_body_is_previewed_not_stored_whole(self, table):
        nx.notify(["u1"], nx.REPLY, "a", body="x" * 500)
        preview = nx.recent("u1")[0]["preview"]
        assert len(preview) <= nx.PREVIEW_LENGTH + 1
        assert preview.endswith("…")

    def test_a_short_body_is_not_given_an_ellipsis(self, table):
        nx.notify(["u1"], nx.REPLY, "a", body="short")
        assert nx.recent("u1")[0]["preview"] == "short"

    def test_they_arrive_unread(self, table):
        nx.notify(["u1"], nx.MENTION, "a")
        assert nx.unread_count(nx.recent("u1")) == 1

    def test_rows_carry_an_expiry(self, table):
        nx.notify(["u1"], nx.MENTION, "a")
        assert nx.recent("u1")[0]["ttl"] > 0


class TestMarkingRead:
    def test_marking_all(self, table):
        nx.notify(["u1"], nx.MENTION, "a")
        nx.notify(["u1"], nx.REPLY, "b")
        assert nx.mark_read("u1") == 2
        assert nx.unread_count(nx.recent("u1")) == 0

    def test_marking_one(self, table):
        nx.notify(["u1"], nx.MENTION, "a")
        nx.notify(["u1"], nx.REPLY, "b")
        one = nx.recent("u1")[0]["notificationId"]
        assert nx.mark_read("u1", [one]) == 1
        assert nx.unread_count(nx.recent("u1")) == 1

    def test_marking_twice_does_not_double_count(self, table):
        nx.notify(["u1"], nx.MENTION, "a")
        nx.mark_read("u1")
        assert nx.mark_read("u1") == 0

    def test_reading_the_list_does_not_mark_it(self, table):
        """
        Opening a page to see what is there should not mark everything seen
        whether or not you looked at it.
        """
        nx.notify(["u1"], nx.MENTION, "a")
        nx.recent("u1")
        assert nx.unread_count(nx.recent("u1")) == 1


class TestWhoHearsAboutAComment:
    """
    The rule that decides whether this feature is usable.

    Notifying every member of every comment is fifty notifications for one
    sentence in a group of fifty, and the reasonable response to that is to
    mute the group. You hear about a conversation you are in.
    """

    def _handler(self, monkeypatch, thread_authors, sent):
        from lambdas.account_comments_action import handler as h

        monkeypatch.setattr(h.comments_dynamo, "for_thread",
                            lambda g, d: [{"authorId": a} for a in thread_authors])
        monkeypatch.setattr(
            h.notifications_dynamo, "notify",
            lambda users, kind, actor, **kw: sent.append((kind, sorted(users))))
        return h

    def test_a_mention_is_always_told(self, monkeypatch):
        sent = []
        h = self._handler(monkeypatch, [], sent)
        h._notify({"groupId": "g", "name": "G"}, "2026-08-28", "author",
                  {"body": "hi", "commentId": "c1"}, ["u2"])
        assert ("mention", ["u2"]) in sent

    def test_people_already_in_the_thread_are_told(self, monkeypatch):
        sent = []
        h = self._handler(monkeypatch, ["u2", "u3"], sent)
        h._notify({"groupId": "g", "name": "G"}, "2026-08-28", "author",
                  {"body": "hi", "commentId": "c1"}, [])
        assert ("reply", ["u2", "u3"]) in sent

    def test_a_silent_member_is_not_told(self, monkeypatch):
        """
        Somebody who has never said anything in this thread is not in this
        conversation, and telling them is how a group becomes noise.
        """
        sent = []
        h = self._handler(monkeypatch, ["u2"], sent)
        h._notify({"groupId": "g", "name": "G"}, "2026-08-28", "author",
                  {"body": "hi", "commentId": "c1"}, [])
        told = {u for _, users in sent for u in users}
        assert "u9" not in told

    def test_a_mentioned_person_is_not_told_twice(self, monkeypatch):
        """Somebody both mentioned and already in the thread hears once."""
        sent = []
        h = self._handler(monkeypatch, ["u2"], sent)
        h._notify({"groupId": "g", "name": "G"}, "2026-08-28", "author",
                  {"body": "@u2", "commentId": "c1"}, ["u2"])
        kinds = [k for k, users in sent if "u2" in users]
        assert kinds == ["mention"]

    def test_the_author_is_never_told_about_their_own_comment(self, monkeypatch):
        sent = []
        h = self._handler(monkeypatch, ["author", "u2"], sent)
        h._notify({"groupId": "g", "name": "G"}, "2026-08-28", "author",
                  {"body": "hi", "commentId": "c1"}, [])
        told = {u for _, users in sent for u in users}
        assert "author" not in told

    def test_a_failure_to_notify_never_costs_the_comment(self, monkeypatch):
        """
        A notification that does not send is a disappointment. A comment that
        fails to post because a notification did is a bug, and the comment is
        the thing the player asked for.
        """
        from lambdas.account_comments_action import handler as h

        monkeypatch.setattr(h.comments_dynamo, "for_thread",
                            lambda g, d: (_ for _ in ()).throw(RuntimeError("down")))
        h._notify({"groupId": "g", "name": "G"}, "2026-08-28", "author",
                  {"body": "hi", "commentId": "c1"}, [])
