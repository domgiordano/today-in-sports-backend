"""
Career milestone detection.

Every other detector in this project answers "was this game remarkable" from a
single row. Milestones cannot: the 300th win is only the 300th if you have
counted the previous 299, in order. So these walk the whole corpus
chronologically and emit an event at the moment a threshold is crossed.

That distinction is why this module exists separately, and it is also why
milestones are the highest-value detector class remaining. Coverage analysis
showed the surviving empty calendar dates cluster in weeks when the only sport
playing is a *regular season*, and a notable regular-season moment is almost
always a player milestone rather than a remarkable team result.

Two things this deliberately does not do:

  * It does not treat every debut as notable. Thousands of players debut; only
    the ones who went on to long careers are worth a question. Career length is
    used as a computable stand-in for significance, since no "was this player
    famous" field exists in any dataset.
  * It does not claim a games-played count. Retrosheet game logs record
    starters, so what is counted here is starts.
"""

import collections

# Round numbers a fan would recognise. 300 wins in particular is a closed club
# of roughly two dozen pitchers, which is exactly the rarity a quiz wants.
PITCHER_WIN_MILESTONES = (100, 200, 250, 300)

# A debut only becomes interesting in hindsight. This is the number of career
# starts that retroactively makes one worth asking about.
LONG_CAREER_STARTS = 1200
PITCHER_LONG_CAREER_STARTS = 400


def _base(sport, league, game, reason, score, title, facts):
    y, m, d = game["gameDate"].split("-")
    return {
        "sport": sport,
        "league": league,
        "leagueId": game.get("away", {}).get("leagueId") or league,
        "isNegroLeagues": bool(game.get("isNegroLeagues")),
        "reason": reason,
        "notabilityScore": score,
        "gameId": game["gameId"],
        "gameDate": game["gameDate"],
        "year": int(y),
        "mmdd": f"{m}-{d}",
        "title": title,
        "facts": facts,
        # Retrosheet's park code, for map questions. Same reason as the
        # game-level detectors carry it: the code is a fact from the game log,
        # and only the corpus build has the index that turns it into a place.
        "park": game.get("park") or None,
        "sourceName": game["sourceName"],
        "sourceDatasetRef": game["sourceDatasetRef"],
    }


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class PartialCorpusError(ValueError):
    """Raised when a milestone run would produce counts that are simply wrong."""


def assert_complete_corpus(games, earliest_required=1920):
    """
    Milestone counts are only correct over a *complete* career.

    Run against a window, every pitcher whose career began before it starts with
    a silent head start: their "100th win" here may be their real 250th, dated
    years late. Nothing about the output looks wrong — which is exactly why this
    is enforced rather than documented.
    """
    seasons = {int(g["gameDate"][:4]) for g in games if g.get("gameDate")}
    if not seasons:
        raise PartialCorpusError("no dated games supplied")
    first = min(seasons)
    if first > earliest_required:
        raise PartialCorpusError(
            f"corpus starts at {first}; milestone counts require history from "
            f"{earliest_required} or earlier, otherwise careers that began "
            f"before {first} are undercounted and milestones are misdated. "
            f"Pass earliest_required={first} to override deliberately.")
    return first


def pitcher_win_milestones(games, milestones=PITCHER_WIN_MILESTONES,
                           earliest_required=1920):
    """
    Emit an event when a pitcher records a milestone career win.

    Requires games in chronological order; sorts defensively rather than
    trusting the caller, because getting this wrong silently attributes the
    milestone to the wrong date.
    """
    assert_complete_corpus(games, earliest_required)
    ordered = sorted((g for g in games if g.get("gameDate")),
                     key=lambda g: (g["gameDate"], str(g.get("gameId"))))
    targets = set(milestones)
    wins = collections.Counter()
    events = []

    for g in ordered:
        wp = (g.get("players") or {}).get("winningPitcher")
        if not wp:
            continue

        wins[wp["id"]] += 1
        total = wins[wp["id"]]
        if total not in targets:
            continue

        loser = (g["away"] if g["home"].get("isWinner") else g["home"])["team"]
        winner_team = (g["home"] if g["home"].get("isWinner") else g["away"])["team"]

        events.append(_base(
            "mlb", g["away"].get("league") or "MLB", g,
            "pitcher_win_milestone",
            99 if total >= 300 else 92 if total >= 200 else 86,
            f"{wp['name']} recorded his {_ordinal(total)} career win, "
            f"for the {winner_team} against the {loser}",
            {
                "player": wp["name"],
                "playerId": wp["id"],
                "careerWins": total,
                "team": winner_team,
                "opponent": loser,
            },
        ))
    return events


def _appearances(games):
    """playerId -> ordered list of (date, game, role) across the corpus."""
    seen = collections.defaultdict(list)
    ordered = sorted((g for g in games if g.get("gameDate")),
                     key=lambda g: (g["gameDate"], str(g.get("gameId"))))

    for g in ordered:
        players = g.get("players") or {}
        for entry in players.get("lineups") or []:
            seen[entry["id"]].append((g["gameDate"], g, entry["name"], "batter"))
        for key in ("awayStarter", "homeStarter"):
            p = players.get(key)
            if p:
                seen[p["id"]].append((g["gameDate"], g, p["name"], "pitcher"))
    return seen


def debut_and_finale(games, long_career=LONG_CAREER_STARTS,
                     pitcher_long_career=PITCHER_LONG_CAREER_STARTS):
    """
    First and last appearances of players with long careers.

    A debut is only notable in hindsight, so the threshold is applied to the
    whole career and the event is emitted on the first date.
    """
    all_dates = [g["gameDate"] for g in games if g.get("gameDate")]
    if not all_dates:
        return []
    corpus_first_season = min(all_dates)[:4]
    corpus_last_season = max(all_dates)[:4]

    events = []
    for player_id, apps in _appearances(games).items():
        if not apps:
            continue

        roles = {a[3] for a in apps}
        threshold = pitcher_long_career if roles == {"pitcher"} else long_career
        if len(apps) < threshold:
            continue

        name = apps[0][2]
        first_date, first_game, _, _ = apps[0]
        last_date, last_game, _, _ = apps[-1]

        # A first appearance in the corpus's opening season is almost certainly
        # not a debut — the career began before the data does. Dave Bancroft
        # "debuting" on the first day of a 1920-start corpus is really his 1915
        # career showing through the edge. Same reasoning at the other end: a
        # last appearance in the final season may just be an active player.
        edge_debut = first_date[:4] == corpus_first_season
        edge_finale = last_date[:4] == corpus_last_season

        span_years = int(last_date[:4]) - int(first_date[:4])

        # If the career predates the corpus, the span and start count are
        # truncated, not wrong-by-a-little. Bancroft played 1915-1930; a
        # 1920-start corpus makes that look like ten seasons. Flag it so
        # templates never state a career length they cannot support.
        facts_common = {
            "player": name,
            "playerId": player_id,
            "careerStarts": len(apps),
            "firstGame": first_date,
            "lastGame": last_date,
            "spanYears": span_years,
            "careerFullyObserved": not edge_debut,
        }

        if not edge_debut:
            events.append(_base(
                "mlb", first_game["away"].get("league") or "MLB", first_game,
                "player_debut", 84,
                f"{name} made his first appearance, beginning a career of "
                f"{len(apps)} starts across {span_years} seasons",
                dict(facts_common, milestone="debut"),
            ))
        if not edge_finale:
            events.append(_base(
                "mlb", last_game["away"].get("league") or "MLB", last_game,
                "player_finale", 82,
                f"{name} made his final appearance after {len(apps)} career starts",
                dict(facts_common, milestone="finale"),
            ))
    return events


def run(games):
    """All milestone events for a corpus of games."""
    return pitcher_win_milestones(games) + debut_and_finale(games)
