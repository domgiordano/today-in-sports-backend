"""
Retrosheet biographical file - the full name behind a player ID.

The game logs and the transaction database both identify players by Retrosheet
ID, and both carry a display name alongside it. For modern games that name is
complete ("Roger Clemens"). For the nineteenth century it is often the surname
alone, and a quiz question whose answer is "Keefe" reads as a data gap rather
than an answer - which is exactly what the review flag was telling us. Before
1900 the game logs frequently carry no name at all.

This file closes that. 21,913 players, of whom 2,167 of the 2,202 who debuted
before 1900 have a first name recorded.

The one judgement here is which name to show. The file has three:

    LAST      Caruthers
    FIRST     Robert Lee        <- full given name, including middle
    NICKNAME  Bob

`FIRST` is the birth-certificate name, so building from it yields "Robert Lee
Caruthers" - correct, useless, and not what anyone would type. The name the
player was known by is in NICKNAME, so that wins when it exists and the first
token of FIRST is the fallback. That gives Bob Caruthers, John Clarkson, Pud
Galvin and Tim Keefe, which is what a quiz needs.

Nothing here asserts anything. It is a lookup from an ID to a name published by
the same source the ID came from.
"""

import csv
import io
import os
import time

from lambdas.common.logger import get_logger

log = get_logger(__file__)

BIOFILE_URL = "https://www.retrosheet.org/BIOFILE.TXT"

# The file is ~4MB and changes when a biographical detail is corrected, which is
# rarely and never in a way that matters mid-build.
CACHE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

# Retrosheet publishes in latin-1; a handful of names carry accents that blow up
# a naive utf-8 read.
ENCODING = "latin-1"


def _fetch(cache_dir):
    """The raw file, from disk when we already have it."""
    local = os.path.join(cache_dir or ".", "BIOFILE.TXT")
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        if os.path.exists(local):
            age = time.time() - os.path.getmtime(local)
            if age < CACHE_MAX_AGE_SECONDS:
                with open(local, encoding=ENCODING) as fh:
                    return fh.read()

    import urllib.request
    with urllib.request.urlopen(BIOFILE_URL, timeout=120) as resp:
        raw = resp.read().decode(ENCODING)

    if cache_dir:
        with open(local, "w", encoding=ENCODING) as fh:
            fh.write(raw)
    return raw


def load(cache_dir=None):
    """Player ID -> the name to show, for every player with a surname."""
    index = _index_rows(csv.DictReader(io.StringIO(_fetch(cache_dir))))
    log.info(f"biofile: {len(index)} players")
    return index


def _index_rows(rows):
    """The name choice, split out so it can be tested without the network."""
    index = {}
    for row in rows:
        pid = (row.get("PLAYERID") or "").strip()
        last = (row.get("LAST") or "").strip()
        if not pid or not last:
            continue

        nickname = (row.get("NICKNAME") or "").strip()
        # First token only: FIRST is the full given name, so "Robert Lee"
        # would otherwise reach the player's own quiz answer.
        given = (row.get("FIRST") or "").strip().split(" ")[0]

        first = nickname or given
        index[pid] = f"{first} {last}".strip() if first else last
    return index


def display_name(index, player_id, fallback=None):
    """
    The full name for a player ID, or `fallback` when the file has no entry.

    Returning the fallback rather than None keeps the caller's existing name -
    a surname is still better than nothing, and 35 pre-1900 players genuinely
    have no first name on record anywhere.
    """
    if not player_id:
        return fallback
    return index.get(player_id) or fallback


def looks_incomplete(name):
    """
    Whether a display name is a bare surname.

    Used to decide when to reach for the biofile at all: a game log that already
    says "Roger Clemens" needs no help, and overwriting a complete name with a
    lookup would risk replacing the right answer with a different player who
    happens to share an ID prefix.
    """
    return bool(name) and " " not in name.strip()
