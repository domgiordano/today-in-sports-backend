"""
Claimed @handles.

Uniqueness is the whole feature, and the only way to get it here is a
conditional write against the key — not a check-then-write, which two callers
can both pass, and not a GSI, which will happily hold two identical values.
"""

import pytest

from lambdas.common import usernames_dynamo as un


class ConditionalCheckFailedException(Exception):
    """Named to match what botocore raises, since the code matches on it."""


class FakeTable:
    def __init__(self):
        self.rows = {}

    def get_item(self, Key):
        row = self.rows.get(Key["username"])
        return {"Item": row} if row else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item["username"] in self.rows:
            raise ConditionalCheckFailedException("the condition failed")
        self.rows[Item["username"]] = Item

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.rows[Key["username"]]["display"] = ExpressionAttributeValues[":d"]

    def delete_item(self, Key):
        self.rows.pop(Key["username"], None)

    def query(self, IndexName, KeyConditionExpression, Limit=None):
        # The fake stands in for the owner-index: everything this user holds.
        wanted = KeyConditionExpression._values[1]
        items = [r for r in self.rows.values() if r["userId"] == wanted]
        return {"Items": items[:Limit] if Limit else items}


@pytest.fixture
def table(monkeypatch):
    fake = FakeTable()
    monkeypatch.setattr(un, "_table", lambda: fake)
    return fake


class TestValidation:
    @pytest.mark.parametrize("raw,expected", [
        ("dom", "dom"),
        ("@dom", "dom"),          # the @ is decoration, not part of the name
        ("  Dom  ", "dom"),
        ("Dom_2026", "dom_2026"),
    ])
    def test_it_normalises_before_judging(self, raw, expected):
        assert un.validate(raw) == expected

    @pytest.mark.parametrize("raw", ["", "  ", "ab", "a" * 21])
    def test_length_and_emptiness_are_refused(self, raw):
        with pytest.raises(ValueError):
            un.validate(raw)

    @pytest.mark.parametrize("raw", ["dom.giordano", "dom-giordano", "dom giordano",
                                     "dom!", "dòm", "dom@home"])
    def test_characters_that_make_two_names_look_alike_are_refused(self, raw):
        """
        Dots and hyphens are how one handle is made to read as another, and a
        leaderboard is where somebody will try it.
        """
        with pytest.raises(ValueError):
            un.validate(raw)

    @pytest.mark.parametrize("raw", ["admin", "ADMIN", "@support", "official", "api"])
    def test_names_that_would_read_as_the_product_are_reserved(self, raw):
        with pytest.raises(ValueError, match="reserved"):
            un.validate(raw)


class TestClaiming:
    def test_a_free_handle_is_taken(self, table):
        assert un.claim("dom", "u1") == "dom"
        assert un.owner_of("dom") == "u1"

    def test_somebody_elses_handle_is_refused(self, table):
        un.claim("dom", "u1")
        with pytest.raises(ValueError, match="taken"):
            un.claim("dom", "u2")

    def test_case_does_not_make_it_a_different_handle(self, table):
        """'Dom' and 'dom' are one claim, or the uniqueness means nothing."""
        un.claim("dom", "u1")
        with pytest.raises(ValueError, match="taken"):
            un.claim("DOM", "u2")

    def test_reclaiming_your_own_is_not_an_error(self, table):
        """A form that submits an unchanged value should not fail."""
        un.claim("dom", "u1")
        assert un.claim("dom", "u1") == "dom"

    def test_you_can_change_only_the_casing_you_display(self, table):
        un.claim("dom", "u1")
        un.claim("Dom", "u1")
        assert un.current_for("u1") == "Dom"
        assert un.owner_of("dom") == "u1"

    def test_a_race_is_settled_by_the_conditional_write(self, table, monkeypatch):
        """
        Both callers pass the read — the row does not exist yet for either.
        Only one passes the write, which is the point of doing it this way.
        """
        monkeypatch.setattr(un, "owner_of", lambda handle: None)
        assert un.claim("dom", "u1") == "dom"
        with pytest.raises(ValueError, match="taken"):
            un.claim("dom", "u2")

    def test_changing_handle_releases_the_old_one(self, table):
        un.claim("dom", "u1")
        un.claim("domg", "u1")
        assert un.owner_of("dom") is None
        assert un.current_for("u1") == "domg"

    def test_the_old_handle_survives_a_failed_change(self, table):
        """
        The release happens only once the new one is held, so a rejected claim
        leaves the player with the name they had rather than with none.
        """
        un.claim("dom", "u1")
        un.claim("taken", "u2")
        with pytest.raises(ValueError):
            un.claim("taken", "u1")
        assert un.current_for("u1") == "dom"


class TestLookups:
    def test_an_unclaimed_handle_has_no_owner(self, table):
        assert un.owner_of("nobody") is None

    def test_a_user_with_no_handle_has_none(self, table):
        assert un.current_for("u1") is None

    def test_releasing_gives_everything_back(self, table):
        un.claim("dom", "u1")
        un.release_all("u1")
        assert un.owner_of("dom") is None
