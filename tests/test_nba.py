"""
NBA adapter and detector tests.

None of these need the API key. The normalizer is tested against real payload
shapes captured from the balldontlie v1 contract, and the detectors against
constructed games — which is the right level anyway, since what matters is
whether a rule fires on the right thing.

Calibration is the live risk in basketball: an 82-game, 30-team season is ~1,230
games, the largest here, so a threshold that feels rare in hockey fires
constantly.
"""

import pytest

from lambdas.common.notability import nba as nb
from lambdas.common.sources import balldontlie as bdl


def raw(gid=1, date="1995-06-14T00:00:00.000Z", home_score=110, away_score=90,
        postseason=False, season=1994, status="Final"):
    """A balldontlie v1 game payload."""
    return {
        "id": gid,
        "date": date,
        "season": season,
        "status": status,
        "postseason": postseason,
        "home_team_score": home_score,
        "visitor_team_score": away_score,
        "home_team": {"id": 14, "abbreviation": "LAL", "city": "Los Angeles",
                      "name": "Lakers", "full_name": "Los Angeles Lakers"},
        "visitor_team": {"id": 2, "abbreviation": "BOS", "city": "Boston",
                         "name": "Celtics", "full_name": "Boston Celtics"},
    }


class TestNormalize:
    def test_maps_the_payload(self):
        g = bdl.normalize(raw())
        assert g["sport"] == "nba"
        assert g["home"]["team"] == "Los Angeles Lakers"
        assert g["away"]["team"] == "Boston Celtics"
        assert g["home"]["isWinner"] is True
        assert g["away"]["isWinner"] is False
        assert g["combinedPoints"] == 200
        assert g["margin"] == 20

    def test_takes_the_local_date_not_the_timestamp(self):
        """`date` is an ISO timestamp; "on this date" means the date part."""
        g = bdl.normalize(raw(date="1995-06-14T23:30:00.000Z"))
        assert g["gameDate"] == "1995-06-14"

    def test_falls_back_to_city_and_nickname(self):
        payload = raw()
        payload["home_team"] = {"abbreviation": "MNL", "city": "Minneapolis",
                                "name": "Lakers"}
        g = bdl.normalize(payload)
        assert g["home"]["team"] == "Minneapolis Lakers"

    def test_unplayed_game_has_no_scores(self):
        g = bdl.normalize(raw(home_score=None, away_score=None))
        assert g["combinedPoints"] is None
        assert g["margin"] is None
        assert g["home"]["isWinner"] is False


class TestCredential:
    def test_missing_key_raises_something_actionable(self, monkeypatch):
        """
        Both lookup paths must fail for this to test anything.

        This originally relied on the test environment having no AWS
        credentials — which stopped being true the moment the parameter was
        created, and the test silently started passing through to a real SSM
        read. A test whose outcome depends on ambient cloud state is not a test,
        so the SSM path is stubbed to fail explicitly.
        """
        monkeypatch.delenv("BALLDONTLIE_API_KEY", raising=False)
        monkeypatch.setattr(bdl, "_api_key", None)

        import builtins
        real_import = builtins.__import__

        def no_boto(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("boto3 unavailable in this test")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_boto)

        with pytest.raises(bdl.MissingCredentialError) as exc:
            bdl.api_key()
        message = str(exc.value)
        assert "aws ssm put-parameter" in message
        assert bdl.SSM_KEY_PATH in message

    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setattr(bdl, "_api_key", None)
        monkeypatch.setenv("BALLDONTLIE_API_KEY", "abc123")
        assert bdl.api_key() == "abc123"
        monkeypatch.setattr(bdl, "_api_key", None)


class TestDetectorCalibration:
    """
    Thresholds must not fire on ordinary games. With ~1,230 games a season, a
    rule that trips on a routine result would swamp every date it touches.
    """

    def test_an_ordinary_game_produces_nothing(self):
        g = bdl.normalize(raw(home_score=102, away_score=98))
        assert nb.run([g]) == []

    def test_a_close_playoff_game_alone_is_not_notable_out_of_season(self):
        """A March playoff game cannot exist; a March regular game is ordinary."""
        g = bdl.normalize(raw(date="1995-03-14T00:00:00.000Z",
                              home_score=101, away_score=99))
        assert nb.run([g]) == []

    @pytest.mark.parametrize("margin,expected", [
        (45, False),   # a 40-point bar fired 22 times in a real season
        (51, True),
    ])
    def test_blowout_threshold(self, margin, expected):
        g = bdl.normalize(raw(home_score=100 + margin, away_score=100))
        fired = [e for e in nb.run([g]) if e["reason"] == "nba_blowout"]
        assert bool(fired) is expected

    def test_playoff_blowout_has_a_lower_bar_than_regular_season(self):
        g = bdl.normalize(raw(home_score=132, away_score=100, postseason=True,
                              date="1995-05-20T00:00:00.000Z"))
        reasons = {e["reason"] for e in nb.run([g], dedupe=False)}
        assert "nba_playoff_blowout" in reasons

    def test_shootout_and_rock_fight(self):
        high = bdl.normalize(raw(gid=1, home_score=150, away_score=140))
        low = bdl.normalize(raw(gid=2, home_score=65, away_score=60))
        assert any(e["reason"] == "nba_shootout" for e in nb.run([high]))
        assert any(e["reason"] == "nba_low_score" for e in nb.run([low]))


class TestFinalsInference:
    """
    The free tier flags a game as postseason but never names the round, so the
    Finals cannot be asserted. June playoff basketball is almost always the
    Finals — the detector says so without claiming a title was won.
    """

    def test_june_playoff_game_is_flagged_as_inferred(self):
        g = bdl.normalize(raw(postseason=True, date="1995-06-14T00:00:00.000Z"))
        e = next(x for x in nb.run([g], dedupe=False)
                 if x["reason"] == "nba_late_playoff")
        assert e["facts"]["roundInferred"] is True
        # It must not claim a championship it cannot know about.
        assert "Finals" not in e["title"]
        assert "won the title" not in e["title"]

    def test_a_may_playoff_game_is_not_treated_as_late(self):
        """
        May is four rounds of playoffs, ~40 games. Including it fired 46 times
        in one measured season, against a 15-per-season calibration bar.
        """
        g = bdl.normalize(raw(postseason=True, date="1995-05-20T00:00:00.000Z",
                              home_score=101, away_score=99))
        assert not [e for e in nb.run([g]) if e["reason"] == "nba_late_playoff"]

    def test_an_april_playoff_game_is_not_treated_as_late(self):
        g = bdl.normalize(raw(postseason=True, date="1995-04-28T00:00:00.000Z",
                              home_score=101, away_score=99))
        assert not [e for e in nb.run([g]) if e["reason"] == "nba_late_playoff"]


class TestOutputShape:
    def test_events_match_the_shared_shape(self):
        g = bdl.normalize(raw(home_score=150, away_score=140))
        for e in nb.run([g]):
            for key in ("sport", "league", "reason", "notabilityScore", "gameId",
                        "gameDate", "year", "mmdd", "title", "facts",
                        "sourceName", "sourceDatasetRef"):
                assert key in e, f"missing {key}"
            assert "None" not in e["title"]
            assert e["sport"] == "nba"

    def test_one_event_per_game(self):
        """A 40-point June playoff game trips three rules."""
        g = bdl.normalize(raw(postseason=True, date="1995-06-14T00:00:00.000Z",
                              home_score=145, away_score=100))
        ids = [e["gameId"] for e in nb.run([g])]
        assert len(ids) == len(set(ids)) == 1
