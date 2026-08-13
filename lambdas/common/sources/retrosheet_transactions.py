"""
Retrosheet transaction database - trades, sales, signings and releases.

Every other MLB source here describes what happened *in* a game. This one
describes what happened between them, which matters for two reasons.

The first is register: a trade is not a stat line. "Who did the Red Sox sell to
the Yankees for $100,000 in December 1919" is a different kind of question from
"how many innings did that game go", and a quiz made only of the latter gets
old fast.

The second is the calendar. Transactions happen when games do not. The one
calendar date the box-score corpus could never fill is Christmas Eve, and there
are 73 day-precise transactions on it. Every one of the 366 dates is covered.

Format is documented in the archive's own readme. Sixteen comma-separated
fields; the ones that matter here are the date, the transaction id (rows sharing
one are a single deal), the player, the type code, and the two team codes.
"""

import collections
import csv
import io
import os
import re
import time
import urllib.request
import zipfile
from datetime import date as _date

from lambdas.common.logger import get_logger

log = get_logger(__file__)

TRAN_URL = "https://www.retrosheet.org/transactions/tranDB.zip"
UA = "today-in-sports/0.1 (+https://todayinsports.app)"

SOURCE_NAME = "Retrosheet transaction database"


class SourceError(Exception):
    pass


# Field positions, per the archive's readme.
F = {
    "date": 0, "time": 1, "approx": 2, "secondary": 3, "secondary_approx": 4,
    "tranId": 5, "player": 6, "type": 7,
    "fromTeam": 8, "fromLeague": 9, "toTeam": 10, "toLeague": 11,
    "draftType": 12, "draftRound": 13, "pickNumber": 14, "info": 15,
}

# Only the types that read as an event a fan would recognise. Deliberately
# narrow: the file also records disabled-list stints, demotions, holdouts and
# military service, none of which make a question anyone wants.
TYPE_LABEL = {
    "T": "trade",
    "P": "purchase",
    "F": "free agent signing",
    "Fo": "free agent signing with first major league team",
    "D": "Rule 5 draft pick",
    "W": "waiver claim",
    "A": "assignment",
    "L": "loan",
    "J": "jumped teams",
    "X": "expansion draft pick",
}

# Voided, returned and refused deals. A question about a trade that was undone
# a week later is a trap, not a question.
REVERSAL_SUFFIXES = ("r", "v", "n")


def _fetch(cache_dir):
    """Download the archive once; later calls reuse the local copy."""
    os.makedirs(cache_dir, exist_ok=True)
    local = os.path.join(cache_dir, "tranDB.zip")
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local

    req = urllib.request.Request(TRAN_URL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=180) as r, open(local, "wb") as f:
            f.write(r.read())
    except Exception as e:
        raise SourceError(f"could not fetch {TRAN_URL}: {e}") from e
    time.sleep(0.5)  # be polite to a free volunteer-run archive
    return local


def _rows(local_zip):
    with zipfile.ZipFile(local_zip) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        raw = z.read(name).decode("latin-1")
    return list(csv.reader(io.StringIO(raw)))


def _clean(v):
    return (v or "").strip().strip('"').strip()


def parse_date(raw):
    """
    A transaction date, or None when it is not precise to the day.

    The archive encodes partial knowledge in the date itself: "00" in the day
    field means the day is unknown, and "0000" in month and day means only that
    it happened before the season started. Those are real transactions with
    unusable dates, and a date-anchored quiz cannot use them. Roughly 5,000 of
    the 101,594 rows fall out here, which is the correct outcome.
    """
    raw = _clean(raw)
    if len(raw) != 8 or not raw.isdigit():
        return None

    year, month, day = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
    if month == 0 or day == 0:
        return None
    try:
        return _date(year, month, day)
    except ValueError:
        return None


def money_amount(info):
    """
    Cash in a deal, in dollars, or None.

    Only 843 rows carry an amount, so this is a garnish rather than a filter.
    It is also era-bound: the $100,000 that bought Babe Ruth in 1919 and the
    $400,000 of a 1977 sale are not remotely comparable, and any ranking on it
    has to be within an era rather than across the whole file.
    """
    m = re.match(r"^\$([0-9,]+)", _clean(info))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def is_reversal(type_code):
    """A deal that was undone. Two characters, second one r/v/n."""
    return len(type_code) == 2 and type_code[1] in REVERSAL_SUFFIXES


def load(cache_dir):
    """
    Every day-precise transaction, grouped into deals.

    Rows sharing a transaction id are one deal - a three-for-two trade is five
    rows. Grouping them is what allows "which four players went the other way"
    rather than five disconnected questions about the same afternoon.
    """
    rows = _rows(_fetch(cache_dir))

    groups = collections.defaultdict(list)
    skipped_date = 0

    for row in rows:
        if len(row) < 16:
            continue

        when = parse_date(row[F["date"]])
        if when is None:
            skipped_date += 1
            continue

        type_code = _clean(row[F["type"]])
        if type_code not in TYPE_LABEL or is_reversal(type_code):
            continue

        # An approximate date is a guess the archive is explicit about. A quiz
        # anchored to the calendar cannot use a guessed day.
        if _clean(row[F["approx"]]) == "@":
            continue

        groups[_clean(row[F["tranId"]])].append({
            "date": when,
            "tranId": _clean(row[F["tranId"]]),
            "playerId": _clean(row[F["player"]]),
            "type": type_code,
            "typeLabel": TYPE_LABEL[type_code],
            "fromTeam": _clean(row[F["fromTeam"]]),
            "fromLeague": _clean(row[F["fromLeague"]]),
            "toTeam": _clean(row[F["toTeam"]]),
            "toLeague": _clean(row[F["toLeague"]]),
            "money": money_amount(row[F["info"]]),
            "info": _clean(row[F["info"]]),
        })

    deals = []
    for tran_id, legs in groups.items():
        when = legs[0]["date"]
        # A deal spanning dates is a data artefact; anchor to the first.
        deals.append({
            "tranId": tran_id,
            "date": when,
            "year": when.year,
            "mmdd": f"{when.month:02d}-{when.day:02d}",
            "type": legs[0]["type"],
            "typeLabel": legs[0]["typeLabel"],
            "legs": legs,
            "money": next((l["money"] for l in legs if l["money"]), None),
            "sourceName": SOURCE_NAME,
            "sourceDatasetRef": TRAN_URL,
        })

    log.info(f"transactions: {len(deals)} usable deals, "
             f"{skipped_date} rows dropped for imprecise dates")
    return deals


# The career index that `notability.transactions` needs is built by the corpus
# builder's milestone accumulator, not here. It already counts appearances and
# roles per player across the whole corpus, and rebuilding it from a list of
# games would mean holding every game in memory - the exact thing the streaming
# design exists to avoid.
