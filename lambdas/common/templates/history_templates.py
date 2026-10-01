"""
"On this day" questions: which team did the thing the day is remembered for.

These replace the score-arithmetic templates ("how many goals did Aston Villa
put past them?") on the same events. The scoreline moves into the prompt as the
reason the day matters, and the question becomes who did it, with three
distractors from the same competition and era.

Each question also carries a one-line `reveal`, shown once the answer is locked
in. Like the prompt, it states only what the event row says. A superlative
("the biggest winning margin of the 2018 regular season") is stated only where
the corpus can prove it - see `_nfl_seasons`. Soccer and baseball cannot: a
title-clinching match keeps only its clinch event, so its score is missing from
the events table, and a 20-run game that was also a star's debut shares that
debut's key. Either could be the season's real record, so neither sport claims
one.
"""

from __future__ import annotations

import collections
import hashlib
import re
import unicodedata

from lambdas.common.notability import nfl as nfl_nb
from lambdas.common.sources import nba_franchises
from lambdas.common.templates import phrasing
from lambdas.common.templates.winter_templates import _nba_club, _nba_season, _q

PICKS = 3

# Pistons 19, Lakers 18 on November 22, 1950 is the lowest-scoring game in NBA
# history. balldontlie carries 1,400+ "low-scoring" games below it, almost all
# 0-0 - rows with no score recorded, not games nobody scored in.
NBA_LOWEST_COMBINED = 37

# How far either side of the season a baseball or hockey pool may reach when
# that one season is too thin to supply three other clubs.
NEAR_YEARS = 3

_SEASON_LABEL = re.compile(r"((?:19|20)\d{2}\s*[/-]\s*\d{2,4})\s*$")


def _norm(name):
    plain = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def _overlaps(a, b):
    """
    The same club under two spellings. openfootball names Real Sociedad both
    "Real Sociedad" and "Real Sociedad de Fútbol", and a distractor that is
    secretly the answer makes two options correct.
    """
    a, b = f" {_norm(a)} ", f" {_norm(b)} "
    return a in b or b in a


def _is_name(team):
    """Retrosheet's earliest seasons leave clubs as codes - NY2, CL4."""
    return bool(team) and any(c.islower() for c in str(team))


def _distractors(answer, exclude, pools, seed):
    """
    Three real clubs, narrowest pool first, none of them the answer or a side
    already named in the prompt. Shuffled by `seed` so one competition does not
    offer the same three wrong answers to every question it produces.
    """
    taken, out = [answer, *exclude], []
    for pool in pools:
        ranked = sorted(set(pool), key=lambda n: hashlib.sha1(f"{seed}|{n}".encode()).hexdigest())
        for name in ranked:
            if not _is_name(name) or any(_overlaps(name, t) for t in taken + out):
                continue
            out.append(name)
            if len(out) == PICKS:
                return out
    return []


def _near(by_year, year):
    wide = [t for y in range(year - NEAR_YEARS, year + NEAR_YEARS + 1) for t in by_year.get(y, [])]
    return [by_year.get(year, []), wide]


def _season(competition):
    match = _SEASON_LABEL.search(str(competition or ""))
    return match.group(1) if match else None


def _in_comp(competition):
    comp = phrasing.competition(competition)
    season = _season(competition)
    return f"the {season} {comp} season" if season else f"the {comp}"


# ------------------------------------------------------------------ soccer

def _soccer_pools(event, ctx):
    f = event["facts"]
    return [ctx["soccer_by_comp"].get(f.get("competition"), []),
            ctx["soccer_by_league"].get(event.get("leagueId"), [])]


def soccer_big_win(event, ctx):
    if event["sport"] != "soccer" or event["reason"] != "soccer_big_win":
        return []
    f = event["facts"]
    w, l, winner, loser = f.get("winningScore"), f.get("losingScore"), f.get("winningTeam"), f.get("losingTeam")
    if None in (w, l) or not winner or not loser or w <= l:
        return []
    picks = _distractors(winner, [loser], _soccer_pools(event, ctx), event["gameId"])
    if not picks:
        return []
    comp, year = phrasing.competition(f["competition"]), event["year"]
    prompt = phrasing.pick([
        f"Which club beat {loser} {w}-{l} in the {comp} on this day in {year}?",
        f"On this day in {year}, {loser} were beaten {w}-{l} in the {comp}. Who beat them?",
        f"Which club put {w} goals past {loser} in the {comp} on this day in {year}?",
    ], event["gameId"], "history_soccer_big_win", winner)
    reveal = (f"{winner} beat {loser} {w}-{l}, a {w - l}-goal winning margin in "
              f"{_in_comp(f['competition'])}.")
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal, answerKind="club")]


def soccer_goal_fest(event, ctx):
    if event["sport"] != "soccer" or event["reason"] != "soccer_goal_fest":
        return []
    f = event["facts"]
    home, away, h, a = f.get("homeTeam"), f.get("awayTeam"), f.get("homeScore"), f.get("awayScore")
    if None in (h, a) or not home or not away:
        return []
    comp, year = phrasing.competition(f["competition"]), event["year"]
    if h > a:
        answer, named = home, away
        prompt = phrasing.pick([
            f"Which club beat {away} {h}-{a} at home in the {comp} on this day in {year}?",
            f"On this day in {year}, {away} lost {h}-{a} away from home in the {comp}. Who beat them?",
        ], event["gameId"], "history_soccer_goal_fest", answer)
    elif a > h:
        answer, named = away, home
        prompt = phrasing.pick([
            f"Which club won {a}-{h} away at {home} in the {comp} on this day in {year}?",
            f"On this day in {year}, {home} lost {h}-{a} at home in the {comp}. Who beat them?",
        ], event["gameId"], "history_soccer_goal_fest", answer)
    else:
        answer, named = away, home
        prompt = (f"{home} drew {h}-{a} at home in the {comp} on this day in {year}. "
                  f"Who were the visitors?")
    picks = _distractors(answer, [named], _soccer_pools(event, ctx), event["gameId"])
    if not picks:
        return []
    reveal = f"{home} {h}-{a} {away}: {h + a} goals in one match in {_in_comp(f['competition'])}."
    return [_q(event, "mc", prompt, answer, distractors=picks, reveal=reveal, answerKind="club")]


# ---------------------------------------------------------------- football

# reason -> (fact, threshold, highest wins, what the record is called, what
# clearing the threshold is called). The thresholds are the detectors' own.
NFL_RECORDS = {
    "regular_season_blowout": ("margin", nfl_nb.BLOWOUT_MARGIN, True,
                               "the biggest winning margin of the {season} regular season",
                               "decided by {n} points or more"),
    "regular_season_shootout": ("combinedPoints", nfl_nb.SHOOTOUT_POINTS, True,
                                "the highest-scoring regular-season game of {season}",
                                "with {n} or more points between the teams"),
    "rock_fight": ("combinedPoints", nfl_nb.ROCK_FIGHT_POINTS, False,
                   "the lowest-scoring regular-season game of {season}",
                   "with {n} points or fewer between the teams"),
}


def _nfl_seasons(events):
    """
    Regular-season margins and totals per season, for complete seasons only.

    This is the one sport where a superlative is provable from events alone.
    Every regular-season game past a threshold fires its detector, and every
    NFL event, whatever reason survived the per-game dedupe, carries the score,
    margin and total. So among one season's events, the games past a threshold
    are all of the games past it. A season counts as complete once its Super
    Bowl is in the corpus; the season in progress never claims a record.
    """
    nfl = [e for e in events if e.get("sport") == "nfl"]
    complete = {e["facts"].get("season") for e in nfl if e.get("reason") == "super_bowl"}
    seasons = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in nfl:
        f = e.get("facts") or {}
        if f.get("round") != "Regular Season" or f.get("season") not in complete:
            continue
        for key in ("margin", "combinedPoints"):
            if f.get(key) is not None:
                seasons[f["season"]][key].append(f[key])
    return seasons


def _nfl_standing(event, ctx):
    """(is it the season's record outright, how many games cleared the bar)."""
    key, bar, highest, _, _ = NFL_RECORDS[event["reason"]]
    f = event["facts"]
    values = ctx["nfl_seasons"].get(f.get("season"), {}).get(key)
    if not values:
        return False, None
    cleared = [v for v in values if (v >= bar if highest else v <= bar)]
    best = max(cleared) if highest else min(cleared)
    return f[key] == best and cleared.count(best) == 1, len(cleared)


def nfl_history(event, ctx):
    if event["sport"] != "nfl" or event["reason"] not in NFL_RECORDS:
        return []
    f = event["facts"]
    winner, loser, w, l = f.get("winningTeam"), f.get("losingTeam"), f.get("winningScore"), f.get("losingScore")
    if None in (w, l) or not winner or not loser or w <= l:
        return []
    season = f.get("season")
    picks = _distractors(winner, [loser], [ctx["nfl_by_season"].get(season, []), ctx["nfl_teams"]],
                         event["gameId"])
    if not picks:
        return []

    key, bar, _, record_text, cleared_text = NFL_RECORDS[event["reason"]]
    record, cleared = _nfl_standing(event, ctx)
    record_text = record_text.format(season=season)
    year = event["year"]
    if record:
        prompt = f"Which team beat the {loser} {w}-{l} on this day in {year}, {record_text}?"
    else:
        prompt = phrasing.pick([
            f"Which team beat the {loser} {w}-{l} on this day in {year}?",
            f"On this day in {year}, the {loser} lost {w}-{l}. Who beat them?",
        ], event["gameId"], "history_nfl", winner)

    detail = (f"a {f['margin']}-point win" if key == "margin"
              else f"{f['combinedPoints']} points between them")
    if record:
        reveal = f"{winner} {w}-{l} {loser}: {detail}, {record_text}."
    elif cleared:
        reveal = (f"{winner} {w}-{l} {loser}: {detail}, one of {cleared} regular-season "
                  f"games in {season} {cleared_text.format(n=bar)}.")
    else:
        reveal = f"{winner} {w}-{l} {loser}: {detail}."
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal)]


# -------------------------------------------------------------- basketball

NBA_REASONS = ("nba_blowout", "nba_playoff_blowout", "nba_shootout", "nba_low_score")


def nba_history(event, ctx):
    if event["sport"] != "nba" or event["reason"] not in NBA_REASONS:
        return []
    f = event["facts"]
    w, l = f.get("winningScore"), f.get("losingScore")
    if None in (w, l) or not 0 < l < w or w + l < NBA_LOWEST_COMBINED:
        return []
    # The answer is a club name, so a season the franchise histories cannot
    # place means no question rather than a modern name on a 1953 game.
    winner, loser = _nba_club(event, ctx, "winningTeam"), _nba_club(event, ctx, "losingTeam")
    if not winner or not loser:
        return []
    # Pooled from clubs that played in this era, not every club in the corpus:
    # the franchise histories cover the 31 that survive, and a folded club
    # resolves to its own name in any season, so a 2021 question offered the
    # Pittsburgh Ironmen.
    season = _nba_season(event)
    pools = [[nba_franchises.team_name(ctx["nba_franchises"], t, season, fallback=None)
              for t in pool] for pool in _near(ctx["nba_by_season"], season)]
    picks = _distractors(winner, [loser], pools, event["gameId"])
    if not picks:
        return []

    playoffs = " in the playoffs" if f.get("isPlayoff") else ""
    year = event["year"]
    prompt = phrasing.pick([
        f"Which team beat the {loser} {w}-{l}{playoffs} on this day in {year}?",
        f"On this day in {year}, the {loser} lost {w}-{l}{playoffs}. Who beat them?",
    ], event["gameId"], "history_nba", winner)
    if event["reason"] in ("nba_blowout", "nba_playoff_blowout"):
        detail = f"a {w - l}-point margin"
    else:
        detail = f"{w + l} points between the two teams"
    reveal = f"{winner} {w}-{l} {loser}{playoffs}: {detail}."
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal)]


# ---------------------------------------------------------------- baseball

def _mlb_pools(event, ctx):
    return _near(ctx["mlb_by_year"][bool(event.get("isNegroLeagues"))], event["year"])


def mlb_blowout(event, ctx):
    if event["sport"] != "mlb" or event["reason"] != "blowout":
        return []
    f = event["facts"]
    team, opp, runs, conceded = f.get("scoringTeam"), f.get("opponent"), f.get("runs"), f.get("opponentRuns")
    if None in (runs, conceded) or not _is_name(team) or not _is_name(opp):
        return []
    picks = _distractors(team, [opp], _mlb_pools(event, ctx), event["gameId"])
    if not picks:
        return []
    year = event["year"]
    prompt = phrasing.pick([
        f"Which team scored {runs} runs against the {opp} on this day in {year}?",
        f"On this day in {year}, the {opp} gave up {runs} runs in one game. To whom?",
    ], event["gameId"], "history_mlb_blowout", team)
    reveal = f"Final score: {team} {runs}, {opp} {conceded}."
    return [_q(event, "mc", prompt, team, distractors=picks, reveal=reveal)]


def mlb_slugfest(event, ctx):
    if event["sport"] != "mlb" or event["reason"] != "slugfest":
        return []
    f = event["facts"]
    away, home, a, h = f.get("awayTeam"), f.get("homeTeam"), f.get("awayRuns"), f.get("homeRuns")
    if None in (a, h) or a == h or not _is_name(away) or not _is_name(home):
        return []
    (winner, w), (loser, l) = sorted([(away, a), (home, h)], key=lambda s: -s[1])
    picks = _distractors(winner, [loser], _mlb_pools(event, ctx), event["gameId"])
    if not picks:
        return []
    year = event["year"]
    prompt = phrasing.pick([
        f"Which team beat the {loser} {w}-{l} on this day in {year}?",
        f"On this day in {year}, the {loser} lost a {w}-{l} slugfest. Who beat them?",
    ], event["gameId"], "history_mlb_slugfest", winner)
    reveal = f"{winner} {w}, {loser} {l}: {w + l} runs between the two teams."
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal)]


def mlb_postseason_shutout(event, ctx):
    if event["sport"] != "mlb" or event["reason"] != "postseason_shutout":
        return []
    f = event["facts"]
    winner, loser, w, ref = f.get("winningTeam"), f.get("losingTeam"), f.get("winningRuns"), f.get("gameRef")
    # "postseason" is the detector's fallback when the source names no round,
    # and "shut out in postseason" says nothing a player can place.
    if not w or ref in (None, "postseason") or not _is_name(winner) or not _is_name(loser):
        return []
    picks = _distractors(winner, [loser], _mlb_pools(event, ctx), event["gameId"])
    if not picks:
        return []
    year = event["year"]
    prompt = phrasing.pick([
        f"Which team shut out the {loser} {w}-0 in {ref} on this day in {year}?",
        f"On this day in {year}, the {loser} were shut out {w}-0 in {ref}. By whom?",
    ], event["gameId"], "history_mlb_postseason_shutout", winner)
    reveal = f"{winner} won {ref} {w}-0, holding the {loser} scoreless."
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal)]


def mlb_world_series(event, ctx):
    """Only the live MLB feed labels World Series games; the cron path makes these."""
    if event["sport"] != "mlb" or event["reason"] not in ("world_series_game", "world_series_game7"):
        return []
    f = event["facts"]
    winner, loser, w, l, n = (f.get("winningTeam"), f.get("losingTeam"), f.get("winningRuns"),
                              f.get("losingRuns"), f.get("gameNumber"))
    if None in (w, l, n) or w <= l or not _is_name(winner) or not _is_name(loser):
        return []
    picks = _distractors(winner, [loser], _mlb_pools(event, ctx), event["gameId"])
    if not picks:
        return []
    year = event["year"]
    prompt = phrasing.pick([
        f"Which team beat the {loser} {w}-{l} in World Series Game {n} on this day in {year}?",
        f"On this day in {year}, the {loser} lost World Series Game {n} {w}-{l}. Who beat them?",
    ], event["gameId"], "history_mlb_world_series", winner)
    decider = ", the deciding Game 7" if n == 7 else ""
    reveal = f"{winner} won World Series Game {n} {w}-{l}{decider}."
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal)]


# ------------------------------------------------------------------ hockey

def nhl_goal_flood(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "goal_flood":
        return []
    f = event["facts"]
    home, away, h, a = f.get("homeTeam"), f.get("awayTeam"), f.get("homeScore"), f.get("awayScore")
    # All-Star sides arrive as bare codes - ASW, CEN - and are not clubs.
    if None in (h, a) or h == a or not _is_name(home) or not _is_name(away):
        return []
    (winner, w), (loser, l) = sorted([(home, h), (away, a)], key=lambda s: -s[1])
    picks = _distractors(winner, [loser], _near(ctx["nhl_by_year"], event["year"]), event["gameId"])
    if not picks:
        return []
    year = event["year"]
    prompt = phrasing.pick([
        f"Which team beat the {loser} {w}-{l} on this day in {year}?",
        f"On this day in {year}, the {loser} lost {w}-{l}. Who beat them?",
    ], event["gameId"], "history_nhl_goal_flood", winner)
    reveal = f"{home} {h}, {away} {a}: {h + a} goals in one game."
    return [_q(event, "mc", prompt, winner, distractors=picks, reveal=reveal)]


TEMPLATES = [
    soccer_big_win, soccer_goal_fest, nfl_history, nba_history,
    mlb_blowout, mlb_slugfest, mlb_postseason_shutout, mlb_world_series, nhl_goal_flood,
]

_TEAM_FACTS = ("winningTeam", "losingTeam", "homeTeam", "awayTeam", "scoringTeam",
               "opponent", "champion", "runnerUp", "throwingTeam", "noHitTeam")


def build_context(events, franchises=None):
    """Distractor pools by competition and era, plus the provable NFL records."""
    # One row per game. The weekly ingest passes stored events alongside the
    # same games freshly detected, and a game counted twice ties with itself.
    events = list({(e.get("sport"), e.get("gameId")): e for e in events}.values())
    soccer_by_comp = collections.defaultdict(list)
    soccer_by_league = collections.defaultdict(list)
    nfl_by_season = collections.defaultdict(list)
    mlb_by_year = {False: collections.defaultdict(list), True: collections.defaultdict(list)}
    nhl_by_year = collections.defaultdict(list)
    nba_by_season = collections.defaultdict(list)
    nfl_teams = []

    for e in events:
        f = e.get("facts") or {}
        teams = [f[k] for k in _TEAM_FACTS if f.get(k)]
        sport = e.get("sport")
        if sport == "soccer":
            soccer_by_comp[f.get("competition")] += teams
            soccer_by_league[e.get("leagueId")] += teams
        elif sport == "nfl":
            nfl_by_season[f.get("season")] += teams
            nfl_teams += teams
        elif sport == "nba":
            nba_by_season[_nba_season(e)] += teams
        elif sport == "mlb":
            mlb_by_year[bool(e.get("isNegroLeagues"))][e.get("year")] += teams
        elif sport == "nhl":
            nhl_by_year[e.get("year")] += teams

    return {
        "soccer_by_comp": soccer_by_comp,
        "soccer_by_league": soccer_by_league,
        "nfl_by_season": nfl_by_season,
        "nfl_teams": list(dict.fromkeys(nfl_teams)),
        "nfl_seasons": _nfl_seasons(events),
        "nba_by_season": nba_by_season,
        "nba_franchises": franchises or {},
        "mlb_by_year": mlb_by_year,
        "nhl_by_year": nhl_by_year,
    }


def generate(events, ctx):
    return [q for event in events for template in TEMPLATES for q in template(event, ctx)]
