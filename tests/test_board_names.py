"""
What the leaderboard calls people.

The live board was showing one person under three identities across eight days
— `Anonymous`, `Dom`, and `dominickj.giordano`, the last being the local part of
their email address published on a public page.
"""

import pytest

from lambdas.play_leaderboard.handler import board_name


class TestSignedInPlayers:
    def test_a_signed_in_player_is_named_from_their_profile(self):
        """
        Not from the round. That is what makes a rename retroactive: change it
        in settings and every board they have appeared on changes with it.
        """
        row = {"anonymous": False, "identity": "sub-1"}
        assert board_name(row, {"sub-1": "Dom"}) == "Dom"

    def test_the_profile_wins_over_a_name_left_on_an_old_round(self):
        """
        Rounds played before the profile name existed carry their own copy.
        The profile is the canonical one, so it overrides.
        """
        row = {"anonymous": False, "identity": "sub-1", "displayName": "old handle"}
        assert board_name(row, {"sub-1": "Dom"}) == "Dom"

    def test_a_signed_in_player_with_no_name_is_not_anonymous(self):
        """
        They are not anonymous — we know exactly who they are, they just have
        not said what to call them. Saying "Anonymous" was wrong and inviting
        them to fix it is the point of the label.
        """
        row = {"anonymous": False, "identity": "sub-1"}
        assert board_name(row, {}) == "Unnamed player"

    def test_an_empty_profile_name_is_not_a_name(self):
        row = {"anonymous": False, "identity": "sub-1"}
        assert board_name(row, {"sub-1": "   "}) == "Unnamed player"


class TestAnonymousPlayers:
    def test_an_anonymous_player_keeps_the_name_they_typed(self):
        """No profile to read, so the round is the only place it can live."""
        row = {"anonymous": True, "displayName": "Dom"}
        assert board_name(row, {}) == "Dom"

    def test_an_anonymous_player_who_never_typed_one_is_anonymous(self):
        assert board_name({"anonymous": True}, {}) == "Anonymous"

    def test_whitespace_is_not_a_name(self):
        assert board_name({"anonymous": True, "displayName": "  "}, {}) == "Anonymous"

    def test_a_row_with_no_anonymous_flag_is_treated_as_anonymous(self):
        """
        The flag defaults to True everywhere else in the codebase, and guessing
        the other way would resolve a device id against the users table.
        """
        assert board_name({"displayName": "Dom"}, {}) == "Dom"


class TestNoEmailEverReachesTheBoard:
    @pytest.mark.parametrize("profile_name", [
        "dominickj.giordano",   # the local part, which is what shipped
        "dominickj.giordano@gmail.com",
    ])
    def test_an_email_is_only_ever_shown_if_someone_typed_it_as_their_name(
            self, profile_name):
        """
        Nothing here derives a name from an address any more. This guards the
        route that did: a signed-in player with no display name. It must reach
        the placeholder, never an identifier we happen to hold.
        """
        row = {"anonymous": False, "identity": "sub-1",
               "email": "dominickj.giordano@gmail.com"}
        assert board_name(row, {}) == "Unnamed player"
        assert "@" not in board_name(row, {})
        assert "giordano" not in board_name(row, {})
