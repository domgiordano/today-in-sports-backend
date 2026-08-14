"""
What an NBA club was called in a given season.

balldontlie is the corpus's basketball source and it answers every question
about a 1953 game with today's franchise: "the Atlanta Hawks and Sacramento
Kings met on January 20, 1953" names two cities neither club had reached. The
payload carries no era signal at all, so nothing downstream could correct it,
and basketball has been sitting on a blunt year cutoff ever since - clubs
simply went unnamed before 2015, which cost most of the basketball inventory.

The NBA's own franchise-history endpoint is the obvious source and is not
usable: stats.nba.com sits behind Akamai and drops non-residential traffic
without answering, so a request hangs until it times out rather than failing.

Wikipedia carries the same table in every franchise infobox, under CC-BY-SA,
in a `history` parameter that reads:

    | history = '''[[Buffalo Braves]]'''<br />1970-1978<br />
                '''San Diego Clippers'''<br />1978-1984<br />
                '''Los Angeles Clippers'''<br />1984-present

which is exactly the mapping needed: a name and the seasons it applied to.
Parsing it gives era-correct names for all 30 current franchises back to 1946.

This asserts nothing. It restates a published table, and every name resolved
through it is attributable to the franchise's own article.

The 14 clubs that folded rather than moved - the Chicago Stags, the Toronto
Huskies, the Waterloo Hawks - already carry their correct historical names,
because a club that never relocated has only ever had one.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request

from lambdas.common.logger import get_logger

log = get_logger(__file__)

API = "https://en.wikipedia.org/w/api.php"

# Wikipedia asks that automated readers identify themselves and a contact.
USER_AGENT = ("today-in-sports/1.0 (https://todayinsports.app; "
              "dominickj.giordano@gmail.com)")

CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

# Franchise names as the corpus holds them, mapped to the article that carries
# the history. Identical in almost every case; balldontlie's abbreviated "LA
# Clippers" is the one that needs saying out loud.
PAGES = {
    "Atlanta Hawks": "Atlanta Hawks",
    "Boston Celtics": "Boston Celtics",
    "Brooklyn Nets": "Brooklyn Nets",
    "Charlotte Hornets": "Charlotte Hornets",
    "Chicago Bulls": "Chicago Bulls",
    "Cleveland Cavaliers": "Cleveland Cavaliers",
    "Dallas Mavericks": "Dallas Mavericks",
    "Denver Nuggets": "Denver Nuggets",
    "Detroit Pistons": "Detroit Pistons",
    "Golden State Warriors": "Golden State Warriors",
    "Houston Rockets": "Houston Rockets",
    "Indiana Pacers": "Indiana Pacers",
    "LA Clippers": "Los Angeles Clippers",
    "Los Angeles Clippers": "Los Angeles Clippers",
    "Los Angeles Lakers": "Los Angeles Lakers",
    "Memphis Grizzlies": "Memphis Grizzlies",
    "Miami Heat": "Miami Heat",
    "Milwaukee Bucks": "Milwaukee Bucks",
    "Minnesota Timberwolves": "Minnesota Timberwolves",
    "New Orleans Pelicans": "New Orleans Pelicans",
    "New York Knicks": "New York Knicks",
    "Oklahoma City Thunder": "Oklahoma City Thunder",
    "Orlando Magic": "Orlando Magic",
    "Philadelphia 76ers": "Philadelphia 76ers",
    "Phoenix Suns": "Phoenix Suns",
    "Portland Trail Blazers": "Portland Trail Blazers",
    "Sacramento Kings": "Sacramento Kings",
    "San Antonio Spurs": "San Antonio Spurs",
    "Toronto Raptors": "Toronto Raptors",
    "Utah Jazz": "Utah Jazz",
    "Washington Wizards": "Washington Wizards",
}

# A season that has not ended yet.
OPEN_ENDED = 9999

_REF = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL | re.IGNORECASE)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Wikipedia writes ranges with an en dash and ends the current one "present".
_RANGE = re.compile(r"(\d{4})\s*[-–—]\s*(\d{4}|present)", re.IGNORECASE)
_YEAR = re.compile(r"\b(\d{4})\b")


def _clean(name):
    """Strip wiki markup from a bold run."""
    name = re.sub(r"<[^>]+>", "", name)
    return name.replace("'''", "").strip()


def _unlink(text):
    """Resolve wikilinks before anything splits on a pipe - [[a|b]] holds one."""
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)
    return re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)


def parse_history(wikitext):
    """
    The `history` infobox parameter as [(name, first_season, last_season)].

    Tokenised rather than matched as whole spans, because the articles use two
    different markups for one table and a whole-span pattern mishandles both.
    The Spurs write theirs as {{ubl|name|years|name|years}}, pipe-separated
    with no line breaks at all. The Hawks use line breaks but give the Buffalo
    Bisons a bare "1946 (NBL)" with no range, which a range-hungry pattern
    skips - and then swallows the following name into it, producing the
    franchise "Buffalo Bisons1946 (NBL)Tri-Cities Blackhawks".

    So: split into tokens, and let each bold name collect the years following
    it until the next bold name. A name carrying several ranges - the Spurs
    played 1973-1976 in the ABA and 1976-present in the NBA, written as two
    tokens under one heading - merges into one span rather than the first
    winning.
    """
    start = wikitext.find("| history")
    if start < 0:
        start = wikitext.find("|history")
    if start < 0:
        return []

    # The parameter ends at the next infobox key at line start.
    block = wikitext[start:]
    end = re.search(r"\n\s*\|\s*\w+\s*=", block[10:])
    if end:
        block = block[:end.start() + 10]

    # Drop the parameter name itself. Skipping the token it shares with the
    # first bold run instead cost every franchise its earliest name, and cost
    # single-name franchises - the Celtics have only ever been the Celtics -
    # every name they have.
    block = re.sub(r"^\|?\s*history\s*=", "", block, count=1)

    block = _REF.sub("", block)
    block = _unlink(block)
    block = _BR.sub("|", block)
    block = block.replace("{{ubl", "").replace("{{unbulleted list", "")
    block = block.replace("}}", "|").replace("{{", "|")

    spans, current = {}, None
    for token in block.split("|"):
        token = token.strip()
        if not token:
            continue

        if "'''" in token:
            current = _clean(token)
            continue
        if current is None:
            continue

        ranges = _RANGE.findall(token)
        if ranges:
            years = [(int(a), OPEN_ENDED if b.lower() == "present" else int(b))
                     for a, b in ranges]
        else:
            # A single season, written bare: "1946 (NBL)".
            bare = _YEAR.findall(token)
            if not bare:
                continue
            years = [(int(y), int(y)) for y in bare]

        first, last = min(y[0] for y in years), max(y[1] for y in years)
        if current in spans:
            spans[current] = (min(spans[current][0], first),
                              max(spans[current][1], last))
        else:
            spans[current] = (first, last)

    return sorted(((n, a, b) for n, (a, b) in spans.items()),
                  key=lambda s: s[1])


def _wikitext(title):
    url = API + "?" + urllib.parse.urlencode({
        "action": "parse", "page": title, "prop": "wikitext",
        "format": "json", "formatversion": "2"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["parse"]["wikitext"]


def load(cache_dir=None, pages=None):
    """
    Franchise -> the names it has carried, with the seasons each applied to.

    Cached as one small JSON file: this is 31 requests against an encyclopedia
    for data that changes when a team relocates, which is roughly never.
    """
    local = os.path.join(cache_dir or ".", "nba_franchises.json")
    if cache_dir and os.path.exists(local):
        if time.time() - os.path.getmtime(local) < CACHE_MAX_AGE_SECONDS:
            with open(local) as fh:
                return json.load(fh)

    index = {}
    for corpus_name, page in sorted((pages or PAGES).items()):
        try:
            spans = parse_history(_wikitext(page))
        except Exception as exc:                       # noqa: BLE001
            log.warning(f"nba franchise history failed for {page}: {exc}")
            continue
        if spans:
            index[corpus_name] = spans
        else:
            log.warning(f"no history parsed for {page}")

    if cache_dir and index:
        os.makedirs(cache_dir, exist_ok=True)
        with open(local, "w") as fh:
            json.dump(index, fh, indent=1)

    log.info(f"nba franchises: {len(index)}")
    return index


def team_name(index, modern_name, season, fallback=None):
    """
    What this franchise was called in `season`.

    Returns None when the answer is not knowable, so a caller can leave the
    club unnamed rather than assert a city it had not reached. That is the
    whole point: a wrong name here is indistinguishable from a right one to
    anybody reading the question.

    A season before the franchise existed returns None too - balldontlie
    occasionally attaches a modern franchise to a game its predecessor did not
    play, and inventing a name for that is the failure this exists to prevent.
    """
    if not modern_name:
        return fallback
    spans = (index or {}).get(modern_name)
    if not spans:
        # A club that folded rather than moved has only ever had one name, and
        # the corpus already holds it.
        return fallback if fallback is not None else modern_name

    season = int(season)
    for name, first, last in spans:
        if first <= season <= last:
            return name
    return None
