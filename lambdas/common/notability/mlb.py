"""
MLB notability detectors — decide which games matter, by rule, from real data.

No model is consulted. Each detector is a pure function over normalized game
rows returning zero or more NotableEvent dicts with a `reason` code, a
`notabilityScore`, and the facts a question template will need.

Correctness guards that are easy to get wrong and matter for trivia:

  * A no-hitter can be LOST. Andy Hawkins threw 8 hitless innings in 1990 and
    lost 4-0. So the no-hit team is not necessarily the losing team, and
    `decisions.winner` is only the right pitcher when the winning pitcher of
    record is on the no-hitting side.
  * Combined no-hitters exist, so a single winner-of-record may not be the
    whole story. Attribution confidence is recorded, never assumed.
  * A rain-shortened hitless game is not an official no-hitter. Require 9+
    innings and a Final status.
"""

from lambdas.common.sources.mlb import _get, is_final

TEAM_SIDES = ("away", "home")


def _other(side):
    return "home" if side == "away" else "away"


def has_usable_teams(game):
    """
    Both team names must be present.

    Some Negro Leagues rows carry a null opponent, which otherwise interpolates
    straight into a prompt as "the None". Cheaper to reject the game than to
    special-case every template.
    """
    return bool(game["away"].get("team")) and bool(game["home"].get("team"))


# MLB's records now officially include the Negro Leagues, and sportId=1 returns
# those games. They are first-class history here, labelled with their real league
# rather than flattened into "MLB".
NEGRO_LEAGUE_IDS = {430, 431, 432, 433, 434, 435, 436, 437}


def _league_of(game):
    for side in ("home", "away"):
        name = game[side].get("league")
        if name:
            return name, game[side].get("leagueId")
    return "Major League Baseball", None


def _base(game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    league_name, league_id = _league_of(game)
    return {
        "sport": "mlb",
        "league": league_name,
        "leagueId": league_id,
        "isNegroLeagues": league_id in NEGRO_LEAGUE_IDS if league_id else False,
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


def detect_no_hitter(game):
    """Team held to 0 hits over a completed game of 9+ innings."""
    if not is_final(game):
        return []
    innings = game.get("innings") or 0
    if innings < 9:
        return []

    out = []
    for side in TEAM_SIDES:
        held = game[side]
        if held.get("hits") != 0:
            continue

        thrower_side = _other(side)
        thrower = game[thrower_side]

        # Attribute the pitcher only when the winner of record is on the
        # no-hitting side. Otherwise the no-hitter was lost or is combined,
        # and we record the uncertainty instead of guessing.
        winner = (game.get("decisions") or {}).get("winner") or {}
        winner_name = winner.get("fullName")
        thrower_won = bool(thrower.get("isWinner"))
        pitcher = winner_name if (thrower_won and winner_name) else None

        facts = {
            "noHitTeam": held["team"],
            "noHitTeamHits": 0,
            "noHitTeamRuns": held.get("runs"),
            "throwingTeam": thrower["team"],
            "throwingTeamRuns": thrower.get("runs"),
            "pitcher": pitcher,
            "attributionConfidence": "high" if pitcher else "unknown",
            "noHitTeamWon": bool(held.get("isWinner")),
            "innings": innings,
        }
        title = (
            f"{thrower['team']} no-hit the {held['team']}"
            if pitcher is None
            else f"{pitcher} no-hit the {held['team']}"
        )
        out.append(_base(game, "no_hitter", 92, title, facts))
    return out


def enrich_from_boxscore(event):
    """
    Second-stage confirmation on no-hitter candidates only, so the extra request
    cost stays proportional to how rare the event is. Does two jobs:

      1. Resolve attribution properly. A no-hitter is only a *solo* no-hitter
         if the no-hitting team used exactly one pitcher. 1991 alone had two
         combined no-hitters (Milacki + 3 on 07-13, Mercker + 2 on 09-11) that
         a winner-of-record check attributes to a single starter and gets wrong.
      2. Confirm a perfect game: no hits, no walks, no hit-by-pitch, and 27
         batters retired in order.
    """
    try:
        box, url = _get(f"game/{event['gameId']}/boxscore", {})
    except Exception:
        # Cannot confirm — downgrade attribution rather than assert it.
        event["facts"]["attributionConfidence"] = "unknown"
        event["facts"]["pitcher"] = None
        return

    teams = box.get("teams") or {}
    f = event["facts"]
    f["sourceDatasetRefBoxscore"] = url

    # --- attribution: how many pitchers did the no-hitting team use?
    for key in ("away", "home"):
        t = teams.get(key) or {}
        if ((t.get("team") or {}).get("name")) != f["throwingTeam"]:
            continue
        used = t.get("pitchers") or []
        f["pitchersUsed"] = len(used)
        if len(used) > 1:
            f["combined"] = True
            f["pitcher"] = None
            f["attributionConfidence"] = "combined"
            f["creditedTo"] = f"the {f['throwingTeam']} pitching staff"
        elif len(used) == 1:
            f["combined"] = False
            f["attributionConfidence"] = "high" if f.get("pitcher") else "unknown"
            f["creditedTo"] = f.get("pitcher")
        break

    # --- perfect game confirmation
    for key in ("away", "home"):
        t = teams.get(key) or {}
        if ((t.get("team") or {}).get("name")) != f["noHitTeam"]:
            continue
        bat = ((t.get("teamStats") or {}).get("batting") or {})
        if bat.get("hits") != 0:
            return
        if bat.get("baseOnBalls", 0) or bat.get("hitByPitch", 0):
            return
        if bat.get("atBats") != 27:
            return
        if f.get("combined"):
            return  # a combined perfect game is a different, rarer claim
        f["perfectGame"] = True
        event["reason"] = "perfect_game"
        event["notabilityScore"] = 99
        event["title"] = (
            f"{f.get('pitcher') or f['throwingTeam']} threw a perfect game "
            f"against the {f['noHitTeam']}"
        )
        return


def detect_world_series_game(game):
    """World Series games, with Game 7s scored highest."""
    if game.get("gameType") != "W" or not is_final(game):
        return []
    n = game.get("seriesGameNumber")
    winner_side = "away" if game["away"].get("isWinner") else "home"
    loser_side = _other(winner_side)
    facts = {
        "gameNumber": n,
        "winningTeam": game[winner_side]["team"],
        "losingTeam": game[loser_side]["team"],
        "winningRuns": game[winner_side].get("runs"),
        "losingRuns": game[loser_side].get("runs"),
        "innings": game.get("innings"),
        "extraInnings": (game.get("innings") or 9) > 9,
    }
    is_g7 = n == 7
    return [_base(
        game,
        "world_series_game7" if is_g7 else "world_series_game",
        96 if is_g7 else 78,
        f"World Series Game {n}: {facts['winningTeam']} beat the "
        f"{facts['losingTeam']} {facts['winningRuns']}-{facts['losingRuns']}",
        facts,
    )]


def detect_marathon(game):
    """15+ inning games."""
    innings = game.get("innings") or 0
    if not is_final(game) or innings < 15:
        return []
    return [_base(game, "extra_innings_marathon", 70,
                  f"{game['away']['team']} at {game['home']['team']} went {innings} innings",
                  {"innings": innings,
                   "awayTeam": game["away"]["team"], "homeTeam": game["home"]["team"],
                   "awayRuns": game["away"].get("runs"), "homeRuns": game["home"].get("runs")})]


def detect_blowout(game):
    """One team scoring 20+."""
    if not is_final(game):
        return []
    out = []
    for side in TEAM_SIDES:
        runs = game[side].get("runs") or 0
        if runs < 20:
            continue
        opp = game[_other(side)]
        out.append(_base(game, "blowout", 68,
                         f"{game[side]['team']} scored {runs} against the {opp['team']}",
                         {"scoringTeam": game[side]["team"], "runs": runs,
                          "opponent": opp["team"], "opponentRuns": opp.get("runs")}))
    return out


def detect_postseason_drama(game):
    """
    Postseason games that decided something or went long. Cheap to compute from
    the schedule payload alone, and postseason games are notable by context in a
    way regular-season games are not.
    """
    gt = game.get("gameType")
    if gt in (None, "R", "S", "E", "A") or not is_final(game):
        return []

    innings = game.get("innings") or 9
    win_side = "away" if game["away"].get("isWinner") else "home"
    lose_side = _other(win_side)
    w, l = game[win_side], game[lose_side]

    # Prefer the API's own round label. Inferring from gameType silently produced
    # "postseason" for Negro Leagues championship games (gameType 'C'), which has
    # no entry in any gameType map.
    label = game.get("seriesDescription") or {
        "W": "World Series", "L": "League Championship Series",
        "D": "Division Series", "F": "Wild Card",
    }.get(gt, "postseason")

    n = game.get("seriesGameNumber")
    # Negro Leagues games carry no seriesGameNumber; never render "Game None".
    game_ref = f"{label} Game {n}" if n else label

    facts = {
        "round": label,
        "gameNumber": n,
        "gameRef": game_ref,
        "winningTeam": w["team"], "losingTeam": l["team"],
        "winningRuns": w.get("runs"), "losingRuns": l.get("runs"),
        "innings": innings,
    }

    out = []
    if innings > 9:
        out.append(_base(game, "postseason_extra_innings", 84,
                         f"{game_ref} went {innings} innings: "
                         f"{w['team']} beat the {l['team']}", facts))
    if (l.get("runs") or 0) == 0:
        out.append(_base(game, "postseason_shutout", 82,
                         f"{game_ref}: {w['team']} shut out "
                         f"the {l['team']} {w.get('runs')}-0", facts))
    if abs((w.get("runs") or 0) - (l.get("runs") or 0)) == 1 and innings == 9:
        out.append(_base(game, "postseason_one_run", 76,
                         f"{game_ref}: {w['team']} edged the "
                         f"{l['team']} {w.get('runs')}-{l.get('runs')}", facts))
    return out


def detect_slugfest(game):
    """Both teams combining for 30+ runs."""
    if not is_final(game):
        return []
    a, h = game["away"].get("runs") or 0, game["home"].get("runs") or 0
    if a + h < 30:
        return []
    return [_base(game, "slugfest", 74,
                  f"{game['away']['team']} and {game['home']['team']} combined for "
                  f"{a + h} runs",
                  {"awayTeam": game["away"]["team"], "homeTeam": game["home"]["team"],
                   "awayRuns": a, "homeRuns": h, "combinedRuns": a + h})]


def detect_one_nothing(game):
    """
    1-0 games that also went to extra innings.

    Calibration note: plain 1-0 games fire ~59 times per season, which is a
    measure of something that merely happens rather than something worth asking
    about. Requiring extra innings drops it into the same rarity band as the
    other detectors. Fire rate per season is the notability proxy — anything
    above roughly 10-15 a season is too common to belong in a history quiz.
    """
    if not is_final(game):
        return []
    a, h = game["away"].get("runs"), game["home"].get("runs")
    if {a, h} != {0, 1}:
        return []
    innings = game.get("innings") or 9
    if innings <= 9:
        return []
    win_side = "away" if a == 1 else "home"
    lose_side = _other(win_side)
    return [_base(game, "one_nothing_extras", 80,
                  f"{game[win_side]['team']} beat the {game[lose_side]['team']} 1-0 "
                  f"in {innings} innings",
                  {"winningTeam": game[win_side]["team"],
                   "losingTeam": game[lose_side]["team"],
                   "innings": innings})]


DETECTORS = [
    detect_no_hitter,
    detect_world_series_game,
    detect_postseason_drama,
    detect_marathon,
    detect_blowout,
    detect_slugfest,
    detect_one_nothing,
]


def dedupe_by_game(events):
    """
    One event per game, highest notabilityScore wins.

    Detectors overlap by design — a World Series Game 7 decided by one run trips
    both `world_series_game7` and `postseason_one_run`. Without this, one game
    yields several events and therefore several questions about the same moment,
    which can put near-duplicates in the same daily quiz.

    A no-hitter also legitimately produces two events (one per team side); those
    share a gameId, and keeping the higher-scoring one is correct because only
    one side was actually held hitless.
    """
    best = {}
    for ev in events:
        key = ev["gameId"]
        if key not in best or ev["notabilityScore"] > best[key]["notabilityScore"]:
            best[key] = ev
    return sorted(best.values(), key=lambda e: (e["gameDate"], -e["notabilityScore"]))


def run(games, enrich=True, dedupe=True):
    events = []
    for g in games:
        if not has_usable_teams(g):
            continue
        for det in DETECTORS:
            for ev in det(g):
                if enrich and ev["reason"] == "no_hitter":
                    enrich_from_boxscore(ev)
                events.append(ev)
    return dedupe_by_game(events) if dedupe else events
