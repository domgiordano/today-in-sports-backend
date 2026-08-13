"""
Detector regression suite — the highest-value tests in phase 1.

Every case is a real event verifiable against the historical record. If one of
these breaks, the corpus is being poisoned, so these run against recorded
fixtures and never the live API.

The negative control matters as much as the positive cases: a detector that
fires on everything is worse than one that fires on nothing.
"""

import pytest

from lambdas.common.notability import mlb as nb


def _no_hit_events(games, boxscores=None, monkeypatch=None):
    """
    Run detectors with boxscore enrichment stubbed from a supplied map.

    Enrichment is what resolves solo-vs-combined attribution, so it cannot be
    skipped, but it must not hit the network in tests.
    """
    if boxscores is not None:
        def fake_get(path, _params):
            gid = int(path.split("/")[1])
            return boxscores[gid], f"https://statsapi.mlb.com/api/v1/{path}"
        monkeypatch.setattr(nb, "_get", fake_get)
    return nb.run(games, enrich=boxscores is not None)


def _box(no_hit_team, throwing_team, pitchers, hits=0, walks=0, hbp=0, at_bats=27):
    return {
        "teams": {
            "away": {
                "team": {"name": no_hit_team},
                "teamStats": {"batting": {
                    "hits": hits, "baseOnBalls": walks,
                    "hitByPitch": hbp, "atBats": at_bats,
                }},
                "pitchers": [1],
            },
            "home": {
                "team": {"name": throwing_team},
                "teamStats": {"batting": {}},
                "pitchers": list(range(pitchers)),
            },
        }
    }


class TestNoHitters:
    def test_solo_no_hitter_is_found_and_attributed(self, games, monkeypatch):
        """1991-05-01 — Nolan Ryan's seventh no-hitter."""
        g = games("1991-05-01")
        events = nb.run(g, enrich=False)
        nh = [e for e in events if e["reason"] == "no_hitter"]

        assert len(nh) == 1
        f = nh[0]["facts"]
        assert f["noHitTeam"] == "Toronto Blue Jays"
        assert f["throwingTeam"] == "Texas Rangers"
        assert f["pitcher"] == "Nolan Ryan Jr."
        assert f["noHitTeamHits"] == 0

    def test_negative_control_finds_nothing(self, games):
        """1991-06-11 — a normal day. A detector that fires here is broken."""
        events = nb.run(games("1991-06-11"), enrich=False)
        assert [e for e in events if e["reason"] in ("no_hitter", "perfect_game")] == []

    @pytest.mark.parametrize("date,team,expected_pitchers", [
        ("1991-07-13", "Baltimore Orioles", 4),   # Milacki, Flanagan, Williamson, Olson
        ("1991-09-11", "Atlanta Braves", 3),      # Mercker, Wohlers, Pena
    ])
    def test_combined_no_hitter_is_not_credited_to_one_pitcher(
        self, games, monkeypatch, date, team, expected_pitchers
    ):
        """
        The bug this guards against shipped once already.

        Checking only "was the winning pitcher on the no-hitting side" is true
        for a combined no-hitter too, so it credited a single starter with a
        game three or four pitchers threw. Attribution now requires the
        no-hitting team to have used exactly one pitcher.
        """
        g = games(date)
        raw = nb.run(g, enrich=False)
        nh = [e for e in raw if e["reason"] == "no_hitter"]
        assert len(nh) == 1, f"expected exactly one no-hitter on {date}"

        ev = nh[0]
        boxes = {ev["gameId"]: _box(
            ev["facts"]["noHitTeam"], team, expected_pitchers, at_bats=30,
        )}
        monkeypatch.setattr(nb, "_get",
                            lambda p, _q: (boxes[int(p.split("/")[1])], "u"))
        nb.enrich_from_boxscore(ev)

        f = ev["facts"]
        assert f["combined"] is True
        assert f["pitchersUsed"] == expected_pitchers
        assert f["pitcher"] is None, "a combined no-hitter must not name one pitcher"
        assert f["attributionConfidence"] == "combined"
        assert team in f["creditedTo"]
        assert ev["reason"] == "no_hitter", "combined games are never perfect games"


class TestPerfectGames:
    @pytest.mark.parametrize("date,no_hit_team,throwing_team", [
        ("1991-07-28", "Los Angeles Dodgers", "Montreal Expos"),   # Martinez
        ("1956-10-08", "Brooklyn Dodgers", "New York Yankees"),    # Larsen
    ])
    def test_perfect_game_confirmed(self, games, monkeypatch, date,
                                    no_hit_team, throwing_team):
        g = games(date)
        ev = [e for e in nb.run(g, enrich=False) if e["reason"] == "no_hitter"][0]
        boxes = {ev["gameId"]: _box(no_hit_team, throwing_team, 1)}
        monkeypatch.setattr(nb, "_get",
                            lambda p, _q: (boxes[int(p.split("/")[1])], "u"))
        nb.enrich_from_boxscore(ev)

        assert ev["reason"] == "perfect_game"
        assert ev["notabilityScore"] == 99
        assert ev["facts"]["perfectGame"] is True

    def test_a_baserunner_disqualifies_a_perfect_game(self, games, monkeypatch):
        """A no-hitter with a walk is still a no-hitter, never a perfect game."""
        ev = [e for e in nb.run(games("1991-07-28"), enrich=False)
              if e["reason"] == "no_hitter"][0]
        boxes = {ev["gameId"]: _box("Los Angeles Dodgers", "Montreal Expos", 1,
                                    walks=1, at_bats=28)}
        monkeypatch.setattr(nb, "_get",
                            lambda p, _q: (boxes[int(p.split("/")[1])], "u"))
        nb.enrich_from_boxscore(ev)

        assert ev["reason"] == "no_hitter"
        assert "perfectGame" not in ev["facts"]


class TestPostseason:
    def test_world_series_game_seven(self, games):
        """1991-10-27 — Twins 1-0 over the Braves in ten innings."""
        events = nb.run(games("1991-10-27"), enrich=False)
        g7 = [e for e in events if e["reason"] == "world_series_game7"]
        assert len(g7) == 1
        f = g7[0]["facts"]
        assert f["winningTeam"] == "Minnesota Twins"
        assert f["losingTeam"] == "Atlanta Braves"
        assert f["gameNumber"] == 7
        assert f["extraInnings"] is True


class TestDataIntegrity:
    def test_one_event_per_game(self, games):
        """
        Detectors overlap by design — a one-run World Series Game 7 trips
        several. Without dedupe, one game yields several near-duplicate
        questions that can land in the same daily quiz.
        """
        for date in ("1991-10-27", "1927-09-17", "1991-05-01"):
            events = nb.run(games(date), enrich=False)
            ids = [e["gameId"] for e in events]
            assert len(ids) == len(set(ids)), f"{date} produced duplicate gameIds"

    def test_negro_leagues_are_labelled_not_flattened(self, games):
        """
        MLB's records officially include the Negro Leagues and sportId=1 returns
        them. They carry their real league name rather than a generic 'MLB'.
        """
        events = nb.run(games("1927-09-17"), enrich=False)
        leagues = {e["league"] for e in events}
        assert any("Negro" in lg for lg in leagues), leagues
        assert all(e["league"] for e in events), "an event must never lack a league"
        assert any(e["isNegroLeagues"] for e in events)

    def test_no_event_carries_a_null_team(self, games):
        """
        Some Negro Leagues rows have a null opponent, which otherwise reaches a
        prompt as the literal string 'None'.
        """
        for date in ("1927-09-17", "1991-05-01"):
            for e in nb.run(games(date), enrich=False):
                assert "None" not in e["title"], f"{date}: {e['title']}"
