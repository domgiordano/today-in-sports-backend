"""
Soccer notability detectors.

Ten leagues at ~350 matches each is ~3,500 matches a season, so calibration
matters as much as it does in basketball. Thresholds here are per league rather
than across all ten, because "a 6-0 win" should be equally notable whichever
league it happened in.

The interesting detector is the title clinch, which — like a career milestone —
cannot be judged from one match. It needs the table walked matchday by matchday
to find the moment a lead became mathematically unassailable. That is real,
dateable, and exactly the kind of thing a date-anchored quiz wants.
"""

import collections

# Per league, per season. A five-goal margin happens a handful of times in a
# 380-match season; eight combined goals is rarer still.
BIG_WIN_MARGIN = 5
GOAL_FEST_COMBINED = 8
HIGH_SCORING_DRAW = 3  # 3-3 or better

POINTS_FOR_WIN = 3


def _base(game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    return {
        "sport": "soccer",
        "league": game["league"],
        "leagueId": game["leagueId"],
        "isNegroLeagues": False,
        "reason": reason,
        "notabilityScore": score,
        "gameId": game["gameId"],
        "gameDate": game["gameDate"],
        "year": int(y),
        "mmdd": f"{m}-{d}",
        "title": title,
        "facts": facts,
        "sourceName": game["sourceName"],
        "sourceDatasetRef": game["sourceDatasetRef"],
    }


def _sides(game):
    if game["home"]["isWinner"]:
        return game["home"], game["away"]
    return game["away"], game["home"]


def detect_big_win(game):
    if game.get("isDraw") or (game.get("margin") or 0) < BIG_WIN_MARGIN:
        return []
    w, l = _sides(game)
    return [_base(game, "soccer_big_win", 82,
                  f"{w['team']} beat {l['team']} {w['score']}-{l['score']}",
                  {"winningTeam": w["team"], "losingTeam": l["team"],
                   "winningScore": w["score"], "losingScore": l["score"],
                   "margin": game["margin"], "competition": game["league"]})]


def detect_goal_fest(game):
    if (game.get("combinedGoals") or 0) < GOAL_FEST_COMBINED:
        return []
    return [_base(game, "soccer_goal_fest", 84,
                  f"{game['home']['team']} and {game['away']['team']} produced "
                  f"{game['combinedGoals']} goals",
                  {"homeTeam": game["home"]["team"], "awayTeam": game["away"]["team"],
                   "homeScore": game["home"]["score"], "awayScore": game["away"]["score"],
                   "combinedGoals": game["combinedGoals"],
                   "competition": game["league"]})]


def detect_high_scoring_draw(game):
    if not game.get("isDraw"):
        return []
    if (game["home"]["score"] or 0) < HIGH_SCORING_DRAW:
        return []
    n = game["home"]["score"]
    return [_base(game, "soccer_high_draw", 80,
                  f"{game['home']['team']} and {game['away']['team']} drew {n}-{n}",
                  {"homeTeam": game["home"]["team"], "awayTeam": game["away"]["team"],
                   "goalsEach": n, "competition": game["league"]})]


def _season_is_complete(total_played):
    """
    Every team must have played the full double round-robin, 2(N-1) matches.

    Returns False for a partial or in-progress season, which is the signal to
    skip clinch detection rather than compute a plausible wrong answer.
    """
    if not total_played:
        return False
    expected = 2 * (len(total_played) - 1)
    return all(n == expected for n in total_played.values())


def detect_title_clinches(games):
    """
    The match after which the leader could no longer be caught.

    Walks each league-season chronologically, maintaining the table, and fires
    on the first date where `leader points > second points + 3 x second's
    remaining matches`. Like a career milestone this is a corpus-level fact, not
    a property of a single match, so it takes the whole season rather than one
    game.
    """
    by_competition = collections.defaultdict(list)
    for g in games:
        by_competition[(g["leagueId"], g["season"])].append(g)

    events = []
    for (league_id, season), matches in by_competition.items():
        matches = sorted(matches, key=lambda g: (g["gameDate"], g["gameId"]))

        total_played = collections.Counter()
        for g in matches:
            total_played[g["home"]["team"]] += 1
            total_played[g["away"]["team"]] += 1

        # A clinch is only correct over a COMPLETE season, and the export is not
        # always complete: Serie A 2024-25 arrives with 370 matches rather than
        # 380, so every team is a match short. That understates what the chasing
        # team can still win, and the title fires days early with a confident,
        # wrong date. In a double round-robin each team plays 2(N-1); anything
        # else means the season cannot be judged.
        if not _season_is_complete(total_played):
            continue

        points = collections.Counter()
        played = collections.Counter()
        clinched = False

        for i, g in enumerate(matches):
            h, a = g["home"], g["away"]
            if g["isDraw"]:
                points[h["team"]] += 1
                points[a["team"]] += 1
            elif h["isWinner"]:
                points[h["team"]] += POINTS_FOR_WIN
            else:
                points[a["team"]] += POINTS_FOR_WIN
            played[h["team"]] += 1
            played[a["team"]] += 1

            if clinched or len(points) < 2:
                continue

            # Only evaluate at the end of a date, so a clinch is attributed to
            # the day it happened rather than to whichever match happened to be
            # processed last within it.
            if i + 1 < len(matches) and matches[i + 1]["gameDate"] == g["gameDate"]:
                continue

            table = points.most_common()
            (leader, lead_pts), (second, second_pts) = table[0], table[1]
            remaining = total_played[second] - played[second]
            if lead_pts > second_pts + POINTS_FOR_WIN * remaining and remaining >= 0:
                clinched = True
                events.append(_base(
                    g, "soccer_title_clinched", 92,
                    f"{leader} clinched the {g['league']} title",
                    {"champion": leader, "runnerUp": second,
                     "points": lead_pts, "runnerUpPoints": second_pts,
                     "matchesRemaining": remaining,
                     "competition": g["league"], "season": season}))
    return events


PER_GAME_DETECTORS = [
    detect_big_win,
    detect_goal_fest,
    detect_high_scoring_draw,
]


def dedupe_by_game(events):
    best = {}
    for ev in events:
        key = ev["gameId"]
        if key not in best or ev["notabilityScore"] > best[key]["notabilityScore"]:
            best[key] = ev
    return sorted(best.values(), key=lambda e: (e["gameDate"], -e["notabilityScore"]))


def run(games, dedupe=True):
    events = []
    for g in games:
        if not g.get("gameDate") or not g["home"].get("team"):
            continue
        for det in PER_GAME_DETECTORS:
            events.extend(det(g))
    events.extend(detect_title_clinches(games))
    return dedupe_by_game(events) if dedupe else events
