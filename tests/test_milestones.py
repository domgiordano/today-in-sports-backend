"""
Career milestone tests.

Milestones are the only detector class that cannot be judged from one row — the
300th win is only the 300th if the previous 299 were counted, in order. That
makes them uniquely prone to silent, plausible-looking errors, so most of these
tests are about what the module refuses to claim.
"""

import pytest

from lambdas.common.notability import milestones as ms


def game(date, gid, winner_pitcher=None, lineup=None, home_wins=True):
    players = {"lineups": lineup or []}
    if winner_pitcher:
        players["winningPitcher"] = {"id": winner_pitcher[0], "name": winner_pitcher[1]}
    return {
        "sport": "mlb",
        "gameId": gid,
        "gameDate": date,
        "away": {"team": "Away Team", "league": "American League",
                 "leagueId": "AL", "isWinner": not home_wins},
        "home": {"team": "Home Team", "league": "American League",
                 "leagueId": "AL", "isWinner": home_wins},
        "players": players,
        "sourceName": "retrosheet",
        "sourceDatasetRef": "ref",
    }


class TestPartialCorpusGuard:
    """
    A milestone run over a window is not slightly wrong — it is confidently
    wrong. A pitcher whose career began before the window starts with a silent
    head start, so their "100th win" may be their real 250th, dated years late.
    Nothing in the output looks suspicious, which is why this raises.
    """

    def test_refuses_a_window_that_starts_too_late(self):
        games = [game("1985-05-01", 1, ("p1", "A Pitcher"))]
        with pytest.raises(ms.PartialCorpusError) as exc:
            ms.pitcher_win_milestones(games)
        assert "1985" in str(exc.value)

    def test_can_be_overridden_deliberately(self):
        games = [game("1985-05-01", 1, ("p1", "A Pitcher"))]
        assert ms.pitcher_win_milestones(games, earliest_required=1985) == []

    def test_refuses_an_empty_corpus(self):
        with pytest.raises(ms.PartialCorpusError):
            ms.assert_complete_corpus([])


class TestPitcherWinMilestones:
    def test_fires_on_the_exact_milestone_win(self):
        games = [game(f"1920-05-{d:02d}", d, ("p1", "A Pitcher"))
                 for d in range(1, 32)]
        # 31 wins — below every threshold.
        assert ms.pitcher_win_milestones(games) == []

    def test_hundredth_win_is_dated_correctly(self):
        games = []
        for i in range(1, 101):
            year = 1920 + (i // 40)
            games.append(game(f"{year}-05-{(i % 28) + 1:02d}", i, ("p1", "Ace")))
        events = ms.pitcher_win_milestones(games)

        assert len(events) == 1
        e = events[0]
        assert e["facts"]["careerWins"] == 100
        assert e["facts"]["player"] == "Ace"
        # It must land on the 100th game chronologically, not the last.
        ordered = sorted(g["gameDate"] for g in games)
        assert e["gameDate"] == ordered[99]

    def test_counts_are_per_pitcher(self):
        games = []
        gid = 0
        for i in range(100):
            gid += 1
            games.append(game(f"1920-06-{(i % 28) + 1:02d}", gid, ("p1", "Ace")))
            gid += 1
            games.append(game(f"1920-06-{(i % 28) + 1:02d}", gid, ("p2", "Other")))
        events = ms.pitcher_win_milestones(games)
        assert {e["facts"]["player"] for e in events} == {"Ace", "Other"}
        assert all(e["facts"]["careerWins"] == 100 for e in events)

    def test_a_300th_win_outscores_a_100th(self):
        def run_to(n):
            games = [game(f"{1920 + i // 100}-05-{(i % 28) + 1:02d}", i, ("p", "P"))
                     for i in range(1, n + 1)]
            return ms.pitcher_win_milestones(games)

        assert run_to(100)[-1]["notabilityScore"] < run_to(300)[-1]["notabilityScore"]


class TestDebutEdgeArtefacts:
    """
    A first appearance in the corpus's opening season is almost never a debut —
    it is a career that began before the data. Dave Bancroft "debuting" on the
    first day of a 1920-start corpus is really his 1915 career showing through
    the edge.
    """

    def _career(self, first_year, last_year, pid="x", name="Player X"):
        games = []
        gid = 0
        for y in range(first_year, last_year + 1):
            for d in range(1, 29):
                gid += 1
                games.append(game(
                    f"{y}-06-{d:02d}", gid,
                    lineup=[{"id": pid, "name": name, "position": "3",
                             "side": "home", "battingOrder": 1}]))
        return games

    def test_no_debut_emitted_in_the_corpus_first_season(self):
        games = self._career(1920, 1980)
        events = ms.debut_and_finale(games)
        assert not [e for e in events if e["reason"] == "player_debut"]

    def test_debut_emitted_when_career_starts_inside_the_corpus(self):
        games = self._career(1920, 1925, pid="edge", name="Edge Player")
        games += self._career(1930, 1990, pid="real", name="Real Player")
        events = ms.debut_and_finale(games)
        debuts = [e for e in events if e["reason"] == "player_debut"]
        assert [e["facts"]["player"] for e in debuts] == ["Real Player"]
        assert debuts[0]["gameDate"].startswith("1930")

    def test_truncated_careers_are_flagged_not_silently_wrong(self):
        """
        The finale may still be real, but the span is truncated. Flagging it is
        what stops a template asking "how many seasons" and stating a number the
        data cannot support.
        """
        # The player starts at the corpus edge but retires well before the
        # corpus ends, so the finale is real while the span is not.
        # 46 seasons x 28 starts = 1288, clearing the 1200 long-career bar.
        games = self._career(1920, 1965, pid="edge", name="Edge Player")
        games += self._career(1975, 1980, pid="later", name="Later Player")
        finales = [e for e in ms.debut_and_finale(games)
                   if e["reason"] == "player_finale"]
        edge = [e for e in finales if e["facts"]["player"] == "Edge Player"]
        assert edge, "a real finale should still be emitted"
        assert edge[0]["facts"]["careerFullyObserved"] is False

    def test_short_careers_are_ignored(self):
        games = self._career(1930, 1931)
        assert ms.debut_and_finale(games) == []


class TestOutputShape:
    def test_events_carry_provenance_and_a_calendar_key(self):
        games = [game(f"{1920 + i // 100}-07-{(i % 28) + 1:02d}", i, ("p", "P"))
                 for i in range(1, 101)]
        for e in ms.pitcher_win_milestones(games):
            assert e["sourceDatasetRef"]
            assert e["sourceName"] == "retrosheet"
            assert len(e["mmdd"]) == 5
            assert "None" not in e["title"]
