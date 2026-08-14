"""
The weekly ingest, and the reason it now covers six sports rather than two.
"""

from lambdas.cron_ingest_recent import handler as h


def test_a_season_that_crosses_new_year_resolves_backwards():
    """
    A January NBA game belongs to the season that began the previous October.
    Asking for the calendar year returns a season that has not started, which
    is indistinguishable from a quiet week.
    """
    assert h._season_of("2026-01-15", first_month=10) == 2025
    assert h._season_of("2025-11-02", first_month=10) == 2025
    # Soccer runs July to May.
    assert h._season_of("2026-03-01", first_month=7) == 2025
    assert h._season_of("2025-08-20", first_month=7) == 2025


def test_only_games_inside_the_window_are_kept():
    """The file-backed sources hand back a whole season, not a date range."""
    season = [{"gameDate": "2026-08-10"}, {"gameDate": "2026-08-13"},
              {"gameDate": "2025-01-01"}]
    kept = h._recent(season, ["2026-08-13", "2026-08-12"])
    assert kept == [{"gameDate": "2026-08-13"}]


def test_one_sport_failing_does_not_cost_the_others(monkeypatch):
    """
    This job covered two sports and reported one number, so a source that had
    moved or rate-limited looked exactly like a quiet week. Each sport is
    isolated and its failure is recorded against its own name.
    """
    def boom(days):
        raise RuntimeError("balldontlie is down")

    monkeypatch.setattr(h, "_ingest_mlb", lambda d: ([{"gameDate": "x"}], []))
    monkeypatch.setattr(h, "_ingest_nhl", lambda d: ([], []))
    monkeypatch.setattr(h, "_ingest_nba", boom)
    monkeypatch.setattr(h, "_ingest_soccer", lambda d: ([], []))
    monkeypatch.setattr(h, "_ingest_nfl", lambda d: ([], []))
    monkeypatch.setattr(h, "_ingest_f1", lambda d: ([], []))
    monkeypatch.setattr(h, "_table", lambda name: _NullTable())
    monkeypatch.setattr(h.nba_franchises, "load", lambda cache_dir=None: {})

    body = h.handler({"lookbackDays": 2}, None)
    import json
    result = json.loads(body["body"])

    assert result["bySport"]["nba"]["error"].startswith("RuntimeError")
    assert result["bySport"]["mlb"]["games"] == 1
    assert result["gamesScanned"] == 1


class _NullTable:
    def put_item(self, **kwargs):
        return {}

    def batch_writer(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_window_straddling_a_season_boundary_fetches_both():
    """
    Eight days in mid-August straddle the end of one soccer season and the
    start of the next. Deriving one season from the newest day silently drops
    everything on the other side, which reads as a quiet week rather than a bug.
    """
    days = ["2026-08-05", "2026-07-30", "2026-06-28"]
    assert h._seasons_spanned(days, first_month=7) == [2026, 2025]
    # A window well inside one season asks for one.
    assert h._seasons_spanned(["2026-03-01", "2026-02-20"], 7) == [2025]
