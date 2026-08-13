"""
Friend groups.

The rules worth pinning are the ones about who can see what: an invite code is
only a secret if it is not handed to people outside the group, and there is
deliberately no way to enumerate groups at all.
"""

from unittest.mock import MagicMock, patch

import pytest

from lambdas.common import groups_dynamo as groups


@pytest.fixture
def table():
    t = MagicMock()
    t.query.return_value = {"Items": []}
    t.update_item.return_value = {"Attributes": {"groupId": "g1"}}
    with patch.object(groups, "_table", return_value=t):
        yield t


# ------------------------------------------------------------- invite codes

def test_a_code_avoids_characters_that_are_read_wrong():
    """0/O, 1/I/L, 5/S and 8/B are the pairs people mishear and mistype."""
    for bad in "OILSB0158":
        assert bad not in groups.CODE_ALPHABET


def test_a_code_cannot_spell_a_word():
    """No vowels, so the generator cannot produce one by accident."""
    for vowel in "AEIOU":
        assert vowel not in groups.CODE_ALPHABET


def test_codes_are_the_expected_shape(table):
    code = groups.generate_code()
    assert len(code) == groups.CODE_LENGTH
    assert set(code) <= set(groups.CODE_ALPHABET)


def test_codes_are_not_all_the_same(table):
    assert len({groups.generate_code() for _ in range(50)}) > 1


def test_no_character_is_twice_as_likely_as_another():
    """A duplicated symbol quietly biases every code the generator makes."""
    assert len(set(groups.CODE_ALPHABET)) == len(groups.CODE_ALPHABET)


# ------------------------------------------------------------------ create

def test_creating_a_group_puts_the_owner_in_it(table):
    group = groups.create_group("The Lads", "u1")
    assert group["ownerId"] == "u1"
    assert group["memberIds"] == {"u1"}
    assert group["name"] == "The Lads"
    assert len(group["inviteCode"]) == groups.CODE_LENGTH


def test_a_group_needs_a_name(table):
    with pytest.raises(ValueError):
        groups.create_group("   ", "u1")


def test_a_colliding_code_is_retried(table):
    """
    Six characters from 24 symbols is ~190 million combinations, so a clash is
    unlikely - but unlikely and handled are different things, and a silent
    collision would drop two groups' members into one.
    """
    calls = {"n": 0}

    def query(**kwargs):
        calls["n"] += 1
        # First code looks taken, second is free.
        return {"Items": [{"groupId": "existing"}]} if calls["n"] == 1 else {"Items": []}

    table.query.side_effect = query
    group = groups.create_group("Test", "u1")
    assert group["inviteCode"]
    assert calls["n"] >= 2


# -------------------------------------------------------------------- join

def test_joining_by_code_adds_a_member(table):
    table.query.return_value = {"Items": [
        {"groupId": "g1", "memberIds": {"u1"}, "name": "x"}]}
    groups.join_group("ABC123", "u2")
    assert table.update_item.called


def test_an_unknown_code_is_rejected(table):
    table.query.return_value = {"Items": []}
    with pytest.raises(ValueError):
        groups.join_group("NOPE12", "u2")


def test_joining_twice_is_harmless(table):
    existing = {"groupId": "g1", "memberIds": {"u1", "u2"}, "name": "x"}
    table.query.return_value = {"Items": [existing]}
    assert groups.join_group("ABC123", "u2") == existing
    assert not table.update_item.called


def test_a_full_group_is_refused(table):
    full = {"groupId": "g1", "name": "x",
            "memberIds": {f"u{i}" for i in range(groups.MAX_MEMBERS)}}
    table.query.return_value = {"Items": [full]}
    with pytest.raises(ValueError, match="full"):
        groups.join_group("ABC123", "newcomer")


# ----------------------------------------------------------------- secrecy

def test_the_invite_code_is_not_shown_to_outsiders():
    """
    Otherwise the code is discoverable by anyone who can name a group id, which
    makes it not a secret.
    """
    group = {"groupId": "g1", "name": "x", "ownerId": "u1",
             "inviteCode": "ABC123", "memberIds": {"u1"}}

    assert "inviteCode" not in groups.public_view(group)
    assert groups.public_view(group, include_code=True)["inviteCode"] == "ABC123"


def test_the_member_list_is_never_exposed():
    """A count is enough; the identities are nobody else's business."""
    group = {"groupId": "g1", "name": "x", "ownerId": "u1",
             "inviteCode": "ABC123", "memberIds": {"u1", "u2", "u3"}}
    view = groups.public_view(group, include_code=True)
    assert view["memberCount"] == 3
    assert "memberIds" not in view


def test_only_the_owner_can_change_the_code(table):
    with patch.object(groups, "get_group",
                      return_value={"groupId": "g1", "ownerId": "u1"}):
        with pytest.raises(PermissionError):
            groups.regenerate_code("g1", "someone-else")
        groups.regenerate_code("g1", "u1")  # the owner may


def test_there_is_no_way_to_list_every_group():
    """
    A searchable directory of small private groups is a harassment surface
    with no upside for a trivia game, so the capability does not exist.
    """
    assert not hasattr(groups, "list_all_groups")
    assert not hasattr(groups, "search_groups")
