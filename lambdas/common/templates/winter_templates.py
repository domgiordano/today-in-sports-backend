"""
Question templates for hockey, motorsport and football.

Same contract as the baseball templates: every prompt is assembled from fields
that came out of a real dataset row, and every answer is a value from that row.
No model is asked what happened.

Distractor sourcing differs by sport and matters more than it looks. Baseball
draws from the same day's real games, which works because 15 games were played.
Hockey and football postseasons have far fewer games per day, so these draw from
other teams in the same event pool — still real, still contemporaneous, never
invented.
"""

import hashlib
from lambdas.common.sources import nba_franchises
from lambdas.common.templates import phrasing

CURRENT_YEAR = 2026

MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def tier_for(year):
    age = CURRENT_YEAR - year
    if age <= 1:
        return 1
    if age <= 5:
        return 2
    if age <= 15:
        return 3
    if age <= 30:
        return 4
    return 5


def pretty_date(gd):
    y, m, d = gd.split("-")
    return f"{MONTHS[int(m)]} {int(d)}, {y}"


def _qid(*parts):
    """
    A question's identity.

    The answer is part of it, and has to be. Several templates build a constant
    prompt - every clue ladder reads "Who is this?" - so hashing only
    (gameId, type, prompt) made the id a function of the game alone. Two players
    who debuted in the same game collided, and one silently overwrote the other:
    61 questions vanished on write, and which copy survived depended on
    generation order, so an approved id could later come to mean a different
    player entirely.
    """
    return hashlib.sha1("|".join(_part(p) for p in parts).encode()).hexdigest()[:16]


def _part(value):
    """Stable text for anything an answer might be: a string, list or dict."""
    if isinstance(value, dict):
        return ",".join(f"{k}={_part(v)}" for k, v in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return ",".join(_part(v) for v in value)
    return str(value)


def _q(event, qtype, prompt, answer, **kw):
    q = {
        "questionId": _qid(event["gameId"], qtype, prompt, answer),
        "type": qtype,
        "tier": tier_for(event["year"]),
        "prompt": prompt,
        "answer": answer,
        "sport": event["sport"],
        "league": event["league"],
        "isNegroLeagues": event.get("isNegroLeagues", False),
        "mmdd": event["mmdd"],
        "year": event["year"],
        # Carried so the assembler can prefer the more notable of two
        # equally-eligible questions. Its tiebreak read this field from the
        # question and no template ever put it there, so every candidate
        # scored zero and selection fell through to a hash of the id.
        "notabilityScore": event.get("notabilityScore"),
        "sourceEventId": event["gameId"],
        "sourceReason": event["reason"],
        "sourceName": event["sourceName"],
        "sourceDatasetRef": event["sourceDatasetRef"],
        "status": "draft",
    }
    q.update(kw)
    return q


def _pick(pool, exclude, n=3):
    """Deterministic, stable distractors drawn from a real pool."""
    seen, out = set(), []
    for item in pool:
        if not item or item in exclude or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) == n:
            break
    return out


# ------------------------------------------------------------------- hockey

def nhl_cup_winner(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "stanley_cup_clincher":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nhl_teams", []), {f["winningTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"Who won the Stanley Cup on {pretty_date(event['gameDate'])}?",
               f["winningTeam"], distractors=pool)]


def nhl_cup_series_length(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "stanley_cup_clincher":
        return []
    f = event["facts"]
    n = f.get("clinchedIn")
    if not n:
        return []
    return [_q(event, "numeric",
               f"The {f['winningTeam']} clinched the Stanley Cup on "
               f"{pretty_date(event['gameDate'])}. In which game of the Final "
               f"did they seal it?",
               n, numericAnswer=n, tolerance=0)]


def nhl_playoff_overtime(event, ctx):
    if event["sport"] != "nhl" or event["reason"] != "playoff_overtime":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nhl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    date = pretty_date(event["gameDate"])
    score = f"{f['winningScore']}-{f['losingScore']}"
    prompt = phrasing.pick([
        (f"On {date}, a {f['round']} game went to overtime and finished "
         f"{score}. Who won it?"),
        (f"A {f['round']} tie needed extra time on {date}, ending {score}. "
         f"Which side came through?"),
        (f"{date}: {score} after overtime in the {f['round']}. Who took it?"),
    ], event["gameId"], "nhl_playoff_ot", f["winningTeam"])
    return [_q(event, "mc", prompt, f["winningTeam"], distractors=pool)]


# --------------------------------------------------------------- motorsport

def f1_decider_winner(event, ctx):
    if event["sport"] != "f1" or event["reason"] != "championship_decider":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("f1_drivers", []), {f["winner"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"The {f['grandPrix']} on {pretty_date(event['gameDate'])} decided "
               f"the drivers' championship. Who won the race itself?",
               f["winner"], distractors=pool)]


def f1_first_win(event, ctx):
    if event["sport"] != "f1" or event["reason"] not in ("first_career_win", "debut_win"):
        return []
    f = event["facts"]
    pool = _pick(ctx.get("f1_drivers", []), {f["winner"]}, 3)
    if len(pool) < 3:
        return []
    debut = event["reason"] == "debut_win"
    return [_q(event, "mc",
               f"Who took his first Grand Prix victory at the {f['grandPrix']} on "
               f"{pretty_date(event['gameDate'])}"
               f"{', on his championship debut' if debut else ''}?",
               f["winner"], distractors=pool)]


def f1_grid_position(event, ctx):
    if event["sport"] != "f1" or event["reason"] != "win_from_the_back":
        return []
    f = event["facts"]
    grid = f.get("gridPosition")
    if grid is None:
        return []
    return [_q(event, "numeric",
               f"{f['winner']} won the {f['grandPrix']} on "
               f"{pretty_date(event['gameDate'])} after starting well down the "
               f"order. What grid position did he start from?",
               grid, numericAnswer=grid, tolerance=1)]


def f1_milestone(event, ctx):
    if event["sport"] != "f1" or event["reason"] != "milestone_win":
        return []
    f = event["facts"]
    n = f.get("careerWins")
    if not n:
        return []
    return [_q(event, "numeric",
               f"{f['winner']} reached a career milestone at the {f['grandPrix']} on "
               f"{pretty_date(event['gameDate'])}. How many Grand Prix wins did "
               f"that make?",
               n, numericAnswer=n, tolerance=0)]


# ----------------------------------------------------------------- football

def nfl_super_bowl_champion(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "super_bowl":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"Who won Super Bowl {f['superBowlNumber']}, played on "
               f"{pretty_date(event['gameDate'])}?",
               f["winningTeam"], distractors=pool)]


def nfl_super_bowl_score(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "super_bowl":
        return []
    f = event["facts"]
    return [_q(event, "numeric",
               f"In Super Bowl {f['superBowlNumber']}, the {f['winningTeam']} beat "
               f"the {f['losingTeam']}. How many points did the "
               f"{f['winningTeam']} score?",
               f["winningScore"], numericAnswer=f["winningScore"], tolerance=3)]


def nfl_playoff_overtime(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "playoff_overtime":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    date = pretty_date(event["gameDate"])
    prompt = phrasing.pick([
        f"A {f['round']} game on {date} went to overtime. Who won it?",
        f"On {date}, a {f['round']} game needed overtime. Which side won?",
        f"{date}: overtime in the {f['round']}. Who came out on top?",
    ], event["gameId"], "nfl_playoff_ot", f["winningTeam"])
    return [_q(event, "mc", prompt, f["winningTeam"], distractors=pool)]


def nfl_shutout_winner(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "shutout":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"On {pretty_date(event['gameDate'])} the {f['losingTeam']} were "
               f"held scoreless. Who shut them out?",
               f["winningTeam"], distractors=pool)]


def nfl_blowout_margin(event, ctx):
    """
    Asks for the beaten side's score. Written first as "beat them 47-10, by how
    many points?", which is the same subtraction test the baseball and
    basketball versions had — the answer sat in the prompt.
    """
    if event["sport"] != "nfl" or event["reason"] != "regular_season_blowout":
        return []
    f = event["facts"]
    if f.get("losingScore") is None or not f.get("winningScore"):
        return []
    conceded = f["losingScore"]
    date = pretty_date(event["gameDate"])
    prompt = phrasing.pick([
        (f"On {date} the {f['winningTeam']} put {f['winningScore']} points on "
         f"the {f['losingTeam']}. How many did the {f['losingTeam']} score?"),
        (f"The {f['winningTeam']} ran up {f['winningScore']} against the "
         f"{f['losingTeam']} on {date}. What did the losers finish on?"),
    ], event["gameId"], "nfl_blowout_margin", conceded)
    return [_q(event, "numeric", prompt, conceded,
               numericAnswer=conceded, tolerance=3)]


def nfl_overtime_winner(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "regular_season_overtime":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    date = pretty_date(event["gameDate"])
    prompt = phrasing.pick([
        f"A game on {date} went to overtime. Who beat the {f['losingTeam']}?",
        f"The {f['losingTeam']} lost in overtime on {date}. To whom?",
        (f"{date}: the {f['losingTeam']} came up short after overtime. Which "
         f"team beat them?"),
    ], event["gameId"], "nfl_regular_ot", f["winningTeam"])
    return [_q(event, "mc", prompt, f["winningTeam"], distractors=pool)]


def nfl_shootout_points(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "regular_season_shootout":
        return []
    f = event["facts"]
    n = f.get("combinedPoints")
    if not n:
        return []
    return [_q(event, "numeric",
               f"The {f['winningTeam']} and {f['losingTeam']} met on "
               f"{pretty_date(event['gameDate'])} in one of the highest-scoring "
               f"games of the era. How many points did the two of them manage "
               f"between them?",
               n, numericAnswer=n, tolerance=6)]


def nfl_rock_fight_points(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "rock_fight":
        return []
    f = event["facts"]
    n = f.get("combinedPoints")
    if n is None:
        return []
    return [_q(event, "numeric",
               f"The {f['winningTeam']} and {f['losingTeam']} met on "
               f"{pretty_date(event['gameDate'])} and barely troubled the "
               f"scoreboard. How many points were scored in the whole game?",
               n, numericAnswer=n, tolerance=2)]


def nfl_one_point_winner(event, ctx):
    if event["sport"] != "nfl" or event["reason"] != "one_point_game":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("nfl_teams", []), {f["winningTeam"], f["losingTeam"]}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"On {pretty_date(event['gameDate'])} the {f['losingTeam']} lost "
               f"by a single point, {f['winningScore']}-{f['losingScore']}. "
               f"Who beat them?",
               f["winningTeam"], distractors=pool)]


TEMPLATES = [
    nhl_cup_winner, nhl_cup_series_length, nhl_playoff_overtime,
    f1_decider_winner, f1_first_win, f1_grid_position, f1_milestone,
    nfl_super_bowl_champion, nfl_super_bowl_score, nfl_playoff_overtime,
    nfl_shutout_winner, nfl_blowout_margin, nfl_overtime_winner,
    nfl_shootout_points, nfl_rock_fight_points, nfl_one_point_winner,
]


def build_context(events, franchises=None):
    """
    Real-name pools for distractors, gathered across the corpus.

    Cross-era pools are acceptable here and arguably better: asking which of
    four genuine NHL franchises won a Cup is a real question, whereas four teams
    that happened to play the same night is often a giveaway when only one
    postseason game was on.
    """
    nhl_teams, f1_drivers, nfl_teams = [], [], []
    nba_teams, soccer_clubs = [], []
    for e in events:
        f = e.get("facts") or {}
        if e["sport"] == "nhl":
            nhl_teams += [f.get("winningTeam"), f.get("losingTeam")]
        elif e["sport"] == "f1":
            f1_drivers.append(f.get("winner"))
        elif e["sport"] == "nfl":
            nfl_teams += [f.get("winningTeam"), f.get("losingTeam")]
        elif e["sport"] == "nba":
            nba_teams += [f.get("winningTeam"), f.get("losingTeam")]
        elif e["sport"] == "soccer":
            soccer_clubs += [f.get("champion"), f.get("runnerUp"),
                             f.get("winningTeam"), f.get("losingTeam"),
                             f.get("homeTeam"), f.get("awayTeam")]

    def uniq(xs):
        return list(dict.fromkeys(x for x in xs if x))

    return {"nhl_teams": uniq(nhl_teams),
            "f1_drivers": uniq(f1_drivers),
            "nfl_teams": uniq(nfl_teams),
            "nba_teams": uniq(nba_teams),
            "nba_franchises": franchises or {},
            "soccer_clubs": uniq(soccer_clubs)}


def generate(events, ctx=None):
    ctx = ctx if ctx is not None else build_context(events)
    out = []
    for ev in events:
        for tpl in TEMPLATES:
            out.extend(tpl(ev, ctx))
    return out


# ---------------------------------------------------------------- basketball

# Basketball is the one sport whose source cannot say what a club was called at
# the time. balldontlie returns the modern franchise name *and* the modern code
# for a 1953 game, so "the Atlanta Hawks and Sacramento Kings met on January 20,
# 1953" names two cities neither club had reached, and nothing in the payload
# could have said otherwise. Three sources were checked for a fix - the teams
# endpoint has no history for surviving franchises, stats.nba.com is
# unreachable, and Wikidata carries six name statements for the whole league.
#
# So the club is simply not named, exactly as a ballpark map question does not
# name the clubs: the question is about the number, and the two teams were only
# ever there for flavour. A question whose *answer* is a club name has no such
# escape and is not built for these years at all.
#
# After this date the modern name is the name it had. It sits after the last
# identity change anyone has made - Charlotte took its name back in 2014.
# Superseded. The names come from the franchise histories now, so there is no
# date before which basketball cannot be named - only clubs the source attaches
# to a season the franchise did not play, which resolve to nothing and are left
# unnamed exactly as before.
NBA_NAMES_RELIABLE_FROM = None


def _nba_season(event):
    """The season a game belongs to, which is not always its calendar year."""
    facts = event.get("facts") or {}
    return int(facts.get("season") or event.get("year") or 0)


def _nba_club(event, ctx, key):
    """
    What this club was called in this season, or None when that is unknowable.

    None matters more than the name: balldontlie attaches the modern Denver
    Nuggets to games from 1949, eighteen years before that franchise existed,
    and inventing a name for those is the exact failure this replaced.
    """
    name = (event.get("facts") or {}).get(key)
    if not name:
        return None
    return nba_franchises.team_name(ctx.get("nba_franchises") or {},
                                    name, _nba_season(event), fallback=None)


def _nba_pool(event, ctx, exclude, n=3):
    """
    Distractors under the names they carried that season.

    An era-correct answer against a pool of modern names gives itself away:
    "Seattle SuperSonics" beside three clubs that did not exist under those
    names in 1988 is solvable without knowing anything about the game.
    """
    season = _nba_season(event)
    index = ctx.get("nba_franchises") or {}
    names = []
    for modern in ctx.get("nba_teams", []):
        era = nba_franchises.team_name(index, modern, season, fallback=None)
        if era:
            names.append(era)
    return _pick(names, exclude, n)


def nba_late_playoff_winner(event, ctx):
    if event["sport"] != "nba" or event["reason"] != "nba_late_playoff":
        return []
    f = event["facts"]
    # The answer is a club name, so a season the source cannot place means no
    # question at all rather than a question with a guessed answer.
    winner = _nba_club(event, ctx, "winningTeam")
    loser = _nba_club(event, ctx, "losingTeam")
    if not winner:
        return []
    pool = _nba_pool(event, ctx, {winner, loser}, 3)
    if len(pool) < 3:
        return []
    # The free-tier payload never names the round, so this says "playoffs"
    # rather than claiming a Finals game -- see notability/nba.py.
    return [_q(event, "mc",
               f"In the {event['year']} NBA playoffs, a game on "
               f"{pretty_date(event['gameDate'])} finished "
               f"{f['winningScore']}-{f['losingScore']}. Who won it?",
               winner, distractors=pool)]


def nba_blowout_margin(event, ctx):
    """
    Asks for the beaten side's score, not the margin.

    Stating the scoreline and asking for the margin is a subtraction test — the
    answer sat in the prompt two words earlier. One side given, the other asked
    for, keeps the anchor without handing over the number.
    """
    if event["sport"] != "nba" or event["reason"] not in ("nba_blowout",
                                                          "nba_playoff_blowout"):
        return []
    f = event["facts"]
    if not f.get("margin") or f.get("losingScore") is None:
        return []
    conceded = f["losingScore"]
    date = pretty_date(event["gameDate"])
    winner = _nba_club(event, ctx, "winningTeam")
    loser = _nba_club(event, ctx, "losingTeam")
    if winner and loser:
        prompt = phrasing.pick([
            (f"On {date}, the {winner} put {f['winningScore']} on the {loser}. "
             f"How many did the {loser} score?"),
            (f"The {winner} ran up {f['winningScore']} against the {loser} on "
             f"{date}. What did the {loser} finish on?"),
        ], event["gameId"], "nba_blowout", conceded)
    else:
        prompt = (f"On {date}, the winning side in an NBA game scored "
                  f"{f['winningScore']}. How many did the losers manage?")
    return [_q(event, "numeric", prompt, conceded,
               numericAnswer=conceded, tolerance=3)]


def nba_combined_points(event, ctx):
    if event["sport"] != "nba" or event["reason"] not in ("nba_shootout",
                                                          "nba_low_score"):
        return []
    f = event["facts"]
    total = f.get("combinedPoints")
    if not total:
        return []
    low = event["reason"] == "nba_low_score"
    band = "low" if low else "high"
    winner = _nba_club(event, ctx, "winningTeam")
    loser = _nba_club(event, ctx, "losingTeam")
    if winner and loser:
        prompt = (f"The {winner} and {loser} met on "
                  f"{pretty_date(event['gameDate'])} in a famously "
                  f"{band}-scoring game. How many points did the two teams "
                  f"score between them?")
    else:
        prompt = (f"Two NBA teams met on {pretty_date(event['gameDate'])} in a "
                  f"famously {band}-scoring game. How many points did they "
                  f"score between them?")
    return [_q(event, "numeric", prompt, total,
               numericAnswer=total, tolerance=8)]


# -------------------------------------------------------------------- soccer

def soccer_title_winner(event, ctx):
    if event["sport"] != "soccer" or event["reason"] != "soccer_title_clinched":
        return []
    f = event["facts"]
    pool = _pick(ctx.get("soccer_clubs", []), {f["champion"], f.get("runnerUp")}, 3)
    if len(pool) < 3:
        return []
    return [_q(event, "mc",
               f"Which club clinched the {phrasing.competition(f['competition'])} title on "
               f"{pretty_date(event['gameDate'])}?",
               f["champion"], distractors=pool, answerKind="club")]


def soccer_title_margin(event, ctx):
    if event["sport"] != "soccer" or event["reason"] != "soccer_title_clinched":
        return []
    f = event["facts"]
    left = f.get("matchesRemaining")
    if left is None:
        return []
    return [_q(event, "numeric",
               f"{f['champion']} sealed the {phrasing.competition(f['competition'])} on "
               f"{pretty_date(event['gameDate'])}. How many matches did the "
               f"runner-up still have left to play?",
               left, numericAnswer=left, tolerance=1)]


def soccer_big_win_score(event, ctx):
    if event["sport"] != "soccer" or event["reason"] != "soccer_big_win":
        return []
    f = event["facts"]
    comp = phrasing.competition(f["competition"])
    date = pretty_date(event["gameDate"])
    prompt = phrasing.pick([
        (f"On {date}, {f['winningTeam']} took {f['losingTeam']} apart in the "
         f"{comp}. How many did they score?"),
        (f"{f['losingTeam']} were overrun by {f['winningTeam']} in the {comp} "
         f"on {date}. How many goals did {f['winningTeam']} put past them?"),
        (f"In the {comp} on {date}, {f['winningTeam']} beat {f['losingTeam']} "
         f"by a margin nobody saw coming. How many did the winners score?"),
    ], event["gameId"], "soccer_big_win", f["winningScore"])
    return [_q(event, "numeric", prompt,
               f["winningScore"], numericAnswer=f["winningScore"], tolerance=1)]


def soccer_goal_fest_total(event, ctx):
    if event["sport"] != "soccer" or event["reason"] != "soccer_goal_fest":
        return []
    f = event["facts"]
    total = f.get("combinedGoals")
    if not total or f.get("homeScore") is None:
        return []
    # One side given, the other asked for. "Produced a remarkable match. How
    # many goals in total?" gave the player nothing to reason from — there was
    # no route to the answer except having memorised that fixture, so it played
    # as a blind guess inside the tolerance. Knowing the home side got four
    # tells you what kind of afternoon it was.
    away = f["awayScore"]
    comp = phrasing.competition(f["competition"])
    date = pretty_date(event["gameDate"])
    prompt = phrasing.pick([
        (f"{f['homeTeam']} scored {f['homeScore']} at home to {f['awayTeam']} "
         f"in the {comp} on {date}. How many did {f['awayTeam']} get?"),
        (f"In a {comp} match on {date}, {f['homeTeam']} managed "
         f"{f['homeScore']} against {f['awayTeam']}. What did the visitors "
         f"finish with?"),
    ], event["gameId"], "soccer_goal_fest", away)
    return [_q(event, "numeric", prompt, away,
               numericAnswer=away, tolerance=1)]


TEMPLATES += [
    nba_late_playoff_winner,
    nba_blowout_margin,
    nba_combined_points,
    soccer_title_winner,
    soccer_title_margin,
    soccer_big_win_score,
    soccer_goal_fest_total,
]
