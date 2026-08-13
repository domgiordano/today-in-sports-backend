"""
Which transactions are worth a question.

87,245 deals survive parsing. Almost all of them are a middle reliever changing
organisations, and asking about one would be indistinguishable from asking about
nothing. The filter is the entire job of this module.

The same substitution the milestone detector makes applies here: no dataset
records whether a player was famous, so **career starts stand in for
significance**. A deal is notable when a player in it went on to - or had
already had - a career long enough that a fan would recognise the name.

That rule has a known and acceptable bias. It favours position players over
pitchers, who start far less often, so the pitcher threshold is separate. It
also misses the player who was traded as a prospect and became a star elsewhere
under a career total that is real but accumulated later - which is fine, because
the career total is what the index counts, not the total at the time of the
deal.

No model is asked whether a trade mattered. Every rule below is arithmetic over
fields in the file.
"""

import collections

# Career starts that make a name recognisable. Deliberately high: at roughly
# eight full seasons of everyday play this is a star, not a regular, and a quiz
# wants the former.
STAR_STARTS = 1200
# Pitchers start once every five days, so the same career is a fifth the starts.
STAR_PITCHER_STARTS = 300

# A deal moving this many players is a blockbuster on size alone, provided
# somebody in it is recognisable.
BLOCKBUSTER_PLAYERS = 4

# Cash deals ranked inside their own decade. Comparing 1919 dollars to 1977
# dollars would rank the whole file by inflation instead of by significance.
TOP_SALES_PER_DECADE = 3


def _is_star(entry):
    if not entry:
        return False
    starts = entry.get("starts", 0)
    return starts >= STAR_STARTS or (
        entry.get("isPitcher") and starts >= STAR_PITCHER_STARTS)


def _named(legs, careers):
    """Players in a deal that we can both name and vouch for."""
    out = []
    for leg in legs:
        entry = careers.get(leg["playerId"])
        if entry and entry.get("name"):
            # isPitcher has to survive this copy: the pitcher threshold is a
            # fifth of the position-player one, and dropping the flag here
            # silently excluded every pitcher who ever played.
            out.append({**leg, "name": entry["name"],
                        "starts": entry.get("starts", 0),
                        "isPitcher": entry.get("isPitcher", False)})
    return out


def _teams(leg, deal, names, resolve):
    """
    Which way THIS player moved, as names in use at the time.

    Direction must come from the headline player's own leg, never from a scan
    across the deal. A trade has legs pointing both ways: when Tris Speaker went
    Boston to Cleveland in 1916, two other players came back the other way, and
    reading the first non-empty code from any leg described the return half of
    the deal while naming Speaker. The output looks entirely plausible and is
    backwards, which is the same failure mode as the postseason games that once
    shifted every career win total.
    """
    from_code, to_code = leg.get("fromTeam"), leg.get("toTeam")
    return (resolve(names, from_code, deal["date"]) if from_code else None,
            resolve(names, to_code, deal["date"]) if to_code else None)


def _event(deal, reason, score, title, facts, players):
    return {
        # "mlb", not "baseball": the router and the assembler's sport-mix rule
        # both key on this exact string, and a variant spelling would make these
        # events invisible to both without erroring anywhere.
        "sport": "mlb",
        "league": "MLB",
        "reason": reason,
        "notabilityScore": score,
        "gameId": f"tran-{deal['tranId']}",
        "gameDate": deal["date"].isoformat(),
        "year": deal["year"],
        "mmdd": deal["mmdd"],
        "title": title,
        "facts": facts,
        "players": players,
        "sourceName": deal["sourceName"],
        "sourceDatasetRef": deal["sourceDatasetRef"],
    }


def detect(deals, careers, team_names, resolve_team):
    """
    Notable transactions, as corpus events.

    `careers` comes from `retrosheet_transactions.career_index`, `team_names`
    and `resolve_team` from the game-log source, so this module holds no
    fetching of its own.
    """
    events = []
    sales_by_decade = collections.defaultdict(list)

    for deal in deals:
        players = _named(deal["legs"], careers)
        if not players:
            continue

        stars = [p for p in players if _is_star(p)]
        headline = max(players, key=lambda p: p["starts"])
        from_team, to_team = _teams(headline, deal, team_names, resolve_team)

        # Both ends of a deal have to be nameable. "Traded to the CL4" is the
        # exact failure the team-name resolver exists to prevent.
        if deal["type"] in ("T", "P", "W", "A") and not (from_team and to_team):
            continue
        if deal["type"] in ("F", "Fo", "D", "X") and not to_team:
            continue

        facts = {
            "player": headline["name"],
            "fromTeam": from_team,
            "toTeam": to_team,
            "transactionType": deal["typeLabel"],
            "playerCount": len(players),
            "allPlayers": [p["name"] for p in players],
        }
        if deal["money"]:
            # `amount` drives the sale-price question, so it is set only where
            # the cash *was* the deal. Money attached to a trade is recorded
            # under a different key so it cannot become "sold for $55,000".
            key = "amount" if deal["type"] == "P" else "cashIncluded"
            facts[key] = deal["money"]

        # Only an outright purchase is a sale. A trade that happened to include
        # cash is still a trade, and calling it one keeps "sold for $55,000"
        # off a question about a deal where players moved both ways.
        if deal["money"] and deal["type"] == "P":
            sales_by_decade[deal["year"] // 10].append((deal, facts, players))

        if len(players) >= BLOCKBUSTER_PLAYERS and stars:
            events.append(_event(
                deal, "blockbuster_trade", 80,
                f"{len(players)}-player deal involving {headline['name']}",
                facts, players))
            continue

        if not stars:
            continue

        if deal["type"] == "T":
            events.append(_event(
                deal, "star_trade", 70,
                f"{headline['name']} traded", facts, players))
        elif deal["type"] in ("F", "Fo"):
            events.append(_event(
                deal, "star_free_agent", 60,
                f"{headline['name']} signs as a free agent", facts, players))
        elif deal["type"] == "P":
            events.append(_event(
                deal, "star_purchase", 65,
                f"{headline['name']} sold", facts, players))
        elif deal["type"] in ("D", "X"):
            events.append(_event(
                deal, "star_drafted", 55,
                f"{headline['name']} selected", facts, players))

    # Landmark sales, ranked inside their own decade so that inflation does not
    # do the ranking for us.
    seen = {e["gameId"] for e in events}
    for decade, entries in sales_by_decade.items():
        entries.sort(key=lambda x: -(x[0]["money"] or 0))
        for deal, facts, players in entries[:TOP_SALES_PER_DECADE]:
            if f"tran-{deal['tranId']}" in seen:
                continue
            events.append(_event(
                deal, "landmark_sale", 75,
                f"{facts['player']} sold for ${deal['money']:,}",
                facts, players))

    return events
