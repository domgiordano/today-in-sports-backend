"""
Friends.

Mutual by design. The tests worth having are about the states that must never
exist: a friendship one side can see and the other cannot, a friendship nobody
asked for, and two people who reached for each other ending up as a pair of
requests and no friendship.
"""

import pytest

from lambdas.common import friends_dynamo as fd


class FakeTable:
    """Enough Dynamo to hold rows and answer the two queries this module makes."""

    def __init__(self):
        self.rows = {}

    def get_item(self, Key):
        row = self.rows.get((Key["userId"], Key["friendId"]))
        return {"Item": row} if row else {}

    def query(self, KeyConditionExpression=None, **_):
        wanted = KeyConditionExpression._values[1]
        return {"Items": [r for (u, _f), r in self.rows.items() if u == wanted]}


class FakeClient:
    def __init__(self, table):
        self.table = table

    def transact_write_items(self, TransactItems):
        for item in TransactItems:
            if "Put" in item:
                row = {k: v["S"] for k, v in item["Put"]["Item"].items()}
                self.table.rows[(row["userId"], row["friendId"])] = row
            else:
                key = item["Delete"]["Key"]
                self.table.rows.pop(
                    (key["userId"]["S"], key["friendId"]["S"]), None)


@pytest.fixture
def store(monkeypatch):
    table = FakeTable()

    class Meta:
        client = FakeClient(table)

    class Resource:
        meta = Meta()

    monkeypatch.setattr(fd, "_table", lambda: table)
    monkeypatch.setattr(fd, "_resource", lambda: Resource())
    return table


def status(store, a, b):
    row = store.rows.get((a, b))
    return row["status"] if row else None


class TestAsking:
    def test_a_request_is_visible_from_both_sides_at_once(self, store):
        # The state this refuses to create: one person believing they asked
        # while the other never hears about it.
        fd.request("ann", "bob")
        assert status(store, "ann", "bob") == fd.PENDING_OUT
        assert status(store, "bob", "ann") == fd.PENDING_IN

    def test_asking_twice_does_not_stack(self, store):
        fd.request("ann", "bob")
        assert fd.request("ann", "bob") == fd.PENDING_OUT
        assert len(store.rows) == 2

    def test_you_cannot_add_yourself(self, store):
        with pytest.raises(ValueError):
            fd.request("ann", "ann")

    def test_reaching_for_each_other_makes_friends_rather_than_deadlock(self, store):
        # Two people adding each other before either answers is the obvious
        # way to end up with two pending requests and no friendship.
        fd.request("ann", "bob")
        assert fd.request("bob", "ann") == fd.ACCEPTED
        assert status(store, "ann", "bob") == fd.ACCEPTED
        assert status(store, "bob", "ann") == fd.ACCEPTED

    def test_asking_somebody_who_is_already_a_friend_changes_nothing(self, store):
        fd.request("ann", "bob")
        fd.accept("bob", "ann")
        assert fd.request("ann", "bob") == fd.ACCEPTED

    def test_the_cap_counts_friends_not_requests(self, store, monkeypatch):
        # A pending request is not a friendship, so it must not consume a slot
        # — otherwise unanswered requests quietly lock somebody out.
        monkeypatch.setattr(fd, "MAX_FRIENDS", 2)
        fd.request("ann", "bob")
        fd.accept("bob", "ann")             # one friend

        fd.request("ann", "cal")            # pending
        fd.request("ann", "dee")            # still allowed: still one friend
        assert fd.counts("ann") == {"accepted": 1, "incoming": 0, "outgoing": 2}

        fd.accept("cal", "ann")             # two friends, now at the cap
        with pytest.raises(ValueError):
            fd.request("ann", "eve")


class TestAccepting:
    def test_both_sides_become_friends_together(self, store):
        fd.request("ann", "bob")
        fd.accept("bob", "ann")
        assert status(store, "ann", "bob") == fd.ACCEPTED
        assert status(store, "bob", "ann") == fd.ACCEPTED

    def test_you_cannot_accept_a_request_nobody_sent(self, store):
        # Otherwise a replayed or forged accept invents a friendship.
        with pytest.raises(ValueError):
            fd.accept("bob", "ann")

    def test_you_cannot_accept_your_own_outgoing_request(self, store):
        fd.request("ann", "bob")
        with pytest.raises(ValueError):
            fd.accept("ann", "bob")


class TestRemoving:
    def test_unfriending_clears_both_sides(self, store):
        # Removing one row would leave the other person seeing a friend who
        # cannot see them, which is worse than either state.
        fd.request("ann", "bob")
        fd.accept("bob", "ann")
        fd.remove("ann", "bob")
        assert store.rows == {}

    def test_declining_clears_both_sides(self, store):
        fd.request("ann", "bob")
        fd.remove("bob", "ann")
        assert store.rows == {}

    def test_withdrawing_your_own_request_clears_both_sides(self, store):
        fd.request("ann", "bob")
        fd.remove("ann", "bob")
        assert store.rows == {}

    def test_removing_somebody_you_have_no_edge_with_is_harmless(self, store):
        fd.remove("ann", "bob")
        assert store.rows == {}


class TestCounts:
    def test_separates_friends_from_the_two_directions_of_pending(self, store):
        fd.request("ann", "bob")
        fd.request("ann", "cal")
        fd.accept("cal", "ann")
        fd.request("dee", "ann")
        assert fd.counts("ann") == {"accepted": 1, "incoming": 1, "outgoing": 1}
