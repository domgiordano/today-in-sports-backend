#!/usr/bin/env python3
"""
Build the event corpus by streaming, one season at a time.

    python scripts/build_corpus.py --from 1871 --to 2026 --out events.jsonl

Replaces the earlier approach of parsing every game into one list and dumping it
to a single JSON. That worked to 1920 and then died: the full 1871-2026 corpus
reached 380 MB in one file and hit ENOSPC. Games are bulky (each carries two
starting lineups); events are not — 150 seasons of games produce a few thousand
events, which is a couple of megabytes.

So games are never all held at once. Each season is parsed, reduced to events,
appended to disk, and dropped.

Career milestones are the wrinkle, because they genuinely span the whole corpus:
the 300th win is only the 300th if the previous 299 were counted. They are
handled by carrying small running counters across seasons — a win tally per
pitcher, and a first-seen/last-seen/appearance-count per player — rather than by
keeping the games those facts came from.
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.common.notability import mlb as mlb_nb          # noqa: E402
from lambdas.common.notability import milestones as ms       # noqa: E402
from lambdas.common.notability import transactions as tran_nb  # noqa: E402
from lambdas.common.sources import retrosheet as rs          # noqa: E402
from lambdas.common.sources import retrosheet_transactions as rst  # noqa: E402

CACHE = os.environ.get("RETROSHEET_CACHE", os.path.expanduser("~/.cache/retrosheet"))


class MilestoneAccumulator:
    """
    Running state for corpus-wide milestones.

    Deliberately tiny: counters and dates, never games. Roughly 20k players
    across all of baseball history, which is a few megabytes rather than a few
    hundred.
    """

    def __init__(self):
        self.wins = collections.Counter()
        self.first_seen = {}
        self.last_seen = {}
        self.appearances = collections.Counter()
        self.names = {}
        self.roles = collections.defaultdict(set)
        self.milestone_events = []
        self.first_season = None
        self.last_season = None
        # Only the games that actually produced a milestone are retained, so an
        # event can carry real provenance without keeping the whole season.
        self.edge_games = {}

    def feed(self, games):
        """
        Career totals count REGULAR-SEASON games only.

        Official career win totals exclude the postseason, and including it
        silently shifts every milestone earlier: feeding postseason games too
        put Randy Johnson's 300th at 2008-08-01 instead of the correct
        2009-06-04, and Seaver's at 1985-07-14 instead of 1985-08-04. The output
        looks entirely plausible, which is what makes it dangerous — the only
        way to catch it is to check a known date.
        """
        regular = [g for g in games
                   if g.get("seriesDescription", "Regular Season") == "Regular Season"]

        for g in sorted(regular, key=lambda x: (x["gameDate"], str(x["gameId"]))):
            season = int(g["gameDate"][:4])
            if self.first_season is None or season < self.first_season:
                self.first_season = season
            if self.last_season is None or season > self.last_season:
                self.last_season = season

            players = g.get("players") or {}

            wp = players.get("winningPitcher")
            if wp:
                self.wins[wp["id"]] += 1
                total = self.wins[wp["id"]]
                if total in ms.PITCHER_WIN_MILESTONES:
                    self.milestone_events.append(
                        self._win_event(g, wp, total))

            for entry in players.get("lineups") or []:
                self._see(entry["id"], entry["name"], g, "batter")
            for key in ("awayStarter", "homeStarter"):
                p = players.get(key)
                if p:
                    self._see(p["id"], p["name"], g, "pitcher")

    def _see(self, pid, name, game, role):
        self.appearances[pid] += 1
        self.names[pid] = name
        self.roles[pid].add(role)
        if pid not in self.first_seen:
            self.first_seen[pid] = game["gameDate"]
            self.edge_games[("first", pid)] = _slim(game)
        self.last_seen[pid] = game["gameDate"]
        self.edge_games[("last", pid)] = _slim(game)

    def _win_event(self, game, pitcher, total):
        loser = (game["away"] if game["home"].get("isWinner") else game["home"])["team"]
        winner_team = (game["home"] if game["home"].get("isWinner") else game["away"])["team"]
        y, m, d = game["gameDate"].split("-")
        return {
            "sport": "mlb",
            "league": game["away"].get("league") or "MLB",
            "leagueId": game["away"].get("leagueId") or "MLB",
            "isNegroLeagues": False,
            "reason": "pitcher_win_milestone",
            "notabilityScore": 99 if total >= 300 else 92 if total >= 200 else 86,
            "gameId": game["gameId"],
            "gameDate": game["gameDate"],
            "year": int(y),
            "mmdd": f"{m}-{d}",
            "title": (f"{pitcher['name']} recorded his {ms._ordinal(total)} career "
                      f"win, for the {winner_team} against the {loser}"),
            "facts": {
                "player": pitcher["name"], "playerId": pitcher["id"],
                "careerWins": total, "team": winner_team, "opponent": loser,
            },
            "sourceName": game["sourceName"],
            "sourceDatasetRef": game["sourceDatasetRef"],
        }

    def career_index(self):
        """
        Player id to name, career starts and role, for the transaction detector.

        This is the same counting the debut and finale events already rely on,
        exposed rather than recomputed. A player who never reached the majors
        has no entry here, which is what stops the thousands of amateur-draft
        rows in the transaction file producing questions about people who never
        played a game.
        """
        return {
            pid: {
                "name": self.names[pid],
                "starts": count,
                "isPitcher": self.roles[pid] == {"pitcher"},
            }
            for pid, count in self.appearances.items()
            if self.names.get(pid)
        }

    def finish(self):
        """Debut and finale events, once the whole corpus has been seen."""
        events = list(self.milestone_events)
        first_year = str(self.first_season)
        last_year = str(self.last_season)

        for pid, count in self.appearances.items():
            threshold = (ms.PITCHER_LONG_CAREER_STARTS
                         if self.roles[pid] == {"pitcher"} else ms.LONG_CAREER_STARTS)
            if count < threshold:
                continue

            first, last = self.first_seen[pid], self.last_seen[pid]
            edge_debut = first[:4] == first_year
            edge_finale = last[:4] == last_year
            span = int(last[:4]) - int(first[:4])
            facts = {
                "player": self.names[pid], "playerId": pid,
                "careerStarts": count, "firstGame": first, "lastGame": last,
                "spanYears": span, "careerFullyObserved": not edge_debut,
            }

            if not edge_debut:
                events.append(_player_event(
                    self.edge_games[("first", pid)], "player_debut", 84,
                    f"{self.names[pid]} made his first appearance, beginning a "
                    f"career of {count} starts across {span} seasons",
                    dict(facts, milestone="debut")))
            if not edge_finale:
                events.append(_player_event(
                    self.edge_games[("last", pid)], "player_finale", 82,
                    f"{self.names[pid]} made his final appearance after {count} "
                    f"career starts",
                    dict(facts, milestone="finale")))
        return events


def _slim(game):
    """Only what an event needs for provenance and titling."""
    return {
        "gameId": game["gameId"], "gameDate": game["gameDate"],
        "away": {"team": game["away"]["team"], "league": game["away"].get("league"),
                 "leagueId": game["away"].get("leagueId")},
        "home": {"team": game["home"]["team"]},
        "sourceName": game["sourceName"],
        "sourceDatasetRef": game["sourceDatasetRef"],
    }


def _player_event(game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    return {
        "sport": "mlb", "league": game["away"].get("league") or "MLB",
        "leagueId": game["away"].get("leagueId") or "MLB",
        "isNegroLeagues": False,
        "reason": reason, "notabilityScore": score,
        "gameId": game["gameId"], "gameDate": game["gameDate"],
        "year": int(y), "mmdd": f"{m}-{d}",
        "title": title, "facts": facts,
        "sourceName": game["sourceName"],
        "sourceDatasetRef": game["sourceDatasetRef"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1871)
    ap.add_argument("--to", dest="end", type=int, default=2026)
    ap.add_argument("--out", required=True, help="JSONL, one event per line")
    args = ap.parse_args()

    print(rs.ATTRIBUTION, "\n")

    acc = MilestoneAccumulator()
    total_games = total_events = 0
    seasons_done = 0

    with open(args.out, "w") as out:
        for year in range(args.start, args.end + 1):
            try:
                games = rs.fetch_season(year, CACHE) + rs.fetch_postseason(year, CACHE)
            except rs.SourceError:
                continue
            if not games:
                continue

            events = mlb_nb.run(games, enrich=False)
            for e in events:
                out.write(json.dumps(e, default=str) + "\n")

            acc.feed(games)
            total_games += len(games)
            total_events += len(events)
            seasons_done += 1
            print(f"  {year}: {len(games):5d} games -> {len(events):3d} events "
                  f"(running {total_events})", flush=True)

            # The point of the exercise: the season is released here.
            del games, events

        milestones = acc.finish()
        for e in milestones:
            out.write(json.dumps(e, default=str) + "\n")

        # Transactions come last because they need the finished career index:
        # whether a 1919 sale was notable depends on a career that ran to 1935.
        deals = rst.load(CACHE)
        team_names = rs.load_team_names(CACHE)
        tran_events = tran_nb.detect(
            deals, acc.career_index(), team_names, rs.team_name)
        for e in tran_events:
            out.write(json.dumps(e, default=str) + "\n")

    print(f"\nseasons   : {seasons_done}")
    print(f"games     : {total_games}")
    print(f"events    : {total_events + len(milestones) + len(tran_events)} "
          f"({total_events} game-level, {len(milestones)} milestone, "
          f"{len(tran_events)} transaction)")
    print(f"corpus    : {acc.first_season}-{acc.last_season}")
    print(f"wrote     : {args.out} ({os.path.getsize(args.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
