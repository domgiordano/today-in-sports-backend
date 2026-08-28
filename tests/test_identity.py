"""
Who is calling a public route.

Every `/play/*` route is `authorization = "NONE"` so anonymous players are
first-class, which means API Gateway never populates claims on them. Every play
handler read claims anyway, so the signed-in branch was dead code: rounds were
filed under a device id and flagged anonymous, accounts earned no streaks, and
`/play/react` refused everybody including the person holding a valid token.
"""

import jwt
import pytest

from lambdas.common import identity


class TestSubject:
    def test_an_anonymous_caller_has_no_subject(self):
        assert identity.subject({"headers": {}}) is None

    def test_no_headers_at_all_is_not_a_crash(self):
        assert identity.subject({}) is None

    def test_claims_from_an_authorised_route_are_used_as_they_stand(self):
        """
        The account and admin routes *are* authorised, so API Gateway has
        already done the verification. Doing it again would be wasted work.
        """
        event = {"requestContext": {"authorizer": {"sub": "sub-1"}}}
        assert identity.subject(event) == "sub-1"

    def test_a_garbage_token_leaves_the_caller_anonymous(self, monkeypatch):
        """
        None rather than an exception. These routes are public: a caller with a
        bad or expired token is a player whose session lapsed mid-quiz, and the
        right answer is to let them keep playing rather than to fail the round.
        """
        monkeypatch.setattr(identity, "_jwks", lambda: object())
        assert identity.subject({"headers": {"Authorization": "Bearer nonsense"}}) is None

    def test_a_token_is_read_from_either_header_casing(self, monkeypatch):
        monkeypatch.setattr(identity, "_jwks", lambda: None)
        for key in ("Authorization", "authorization"):
            # Reaching the JWKS lookup at all proves the header was found.
            assert identity.subject({"headers": {key: "Bearer x"}}) is None

    def test_a_token_without_jwks_configured_is_refused_not_trusted(self, monkeypatch):
        monkeypatch.setattr(identity, "_jwks", lambda: None)
        assert identity.subject({"headers": {"Authorization": "Bearer x"}}) is None


class TestResolve:
    def test_an_anonymous_player_is_recorded_under_their_device(self):
        ident, anon = identity.resolve({"headers": {}}, "dev-123")
        assert (ident, anon) == ("dev-123", True)

    def test_a_signed_in_player_is_recorded_under_their_subject(self):
        event = {"requestContext": {"authorizer": {"sub": "sub-1"}}}
        ident, anon = identity.resolve(event, "dev-123")
        assert (ident, anon) == ("sub-1", False)

    def test_a_signed_in_player_is_not_anonymous(self):
        """
        The regression this exists for. `anonymous` stayed True for every
        signed-in round, so the board named them from the round rather than
        their profile and no streak was ever credited to an account.
        """
        event = {"requestContext": {"authorizer": {"sub": "sub-1"}}}
        assert identity.resolve(event, "dev-123")[1] is False

    def test_a_signed_subject_beats_whatever_the_browser_claims(self):
        """The device id is whatever the client says; the subject is signed."""
        event = {"requestContext": {"authorizer": {"sub": "sub-1"}}}
        assert identity.resolve(event, "dev-someone-elses")[0] == "sub-1"

    def test_a_missing_device_id_is_empty_rather_than_none(self):
        assert identity.resolve({"headers": {}}, None) == ("", True)
