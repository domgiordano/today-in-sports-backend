"""
Guardian sport archive.

The first source here that is prose rather than a dataset, and the only one
whose notability comes from editorial judgement instead of a rule: if a
newspaper ran the story that day, it mattered that day. For narrative events -
somebody named as a starter, a manager sacked, a record broken off the field -
that is a better signal than anything derivable, because no dataset has a field
meaning "was named the starter".

Two things this module refuses to do, and they are the whole reason it is safe
to use at all:

  * **It never asserts a fact.** It returns the article's own sentences with a
    link, and nothing downstream may state anything those sentences do not.
  * **It never guesses a date.** Publication date is not event date - a story
    published on 6 December covers a game played on the 5th - so the event date
    is resolved from the article's own text where possible and the candidate is
    dropped where it is not. Guessing would file the event on a day it did not
    happen, which is the one thing a date-anchored quiz cannot survive.

Coverage runs from 1999. The free tier needs a key; `test` works for a handful
of calls and is rate-limited hard, so it is the default only so that a
developer can see the shape of the data without waiting on a signup.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from lambdas.common.logger import get_logger

log = get_logger(__file__)

BASE = "https://content.guardianapis.com/search"
UA = "today-in-sports/0.1 (+https://todayinsports.app)"

SOURCE_NAME = "The Guardian"

# Coverage begins here. Asking for earlier returns nothing rather than failing,
# which would look like a bug rather than a limit.
EARLIEST_YEAR = 1999

# Sections that are actually sport. The API's own taxonomy, not a guess.
SPORT_SECTIONS = ("sport", "football")

# Headlines that are routine coverage rather than an event. A preview, a live
# blog and a talking-points column are all published every week and none of
# them is a thing that happened.
ROUTINE = re.compile(
    r"\b(preview|live|as it happened|talking points|in pictures|gallery|"
    r"quiz|clockwatch|minute-by-minute|team news|predictions?|"
    r"what to watch|the fiver|rumour|gossip)\b", re.I)


class SourceError(Exception):
    pass


def _key():
    """
    The API key.

    `test` is a real key the Guardian publishes for evaluation. It is rate
    limited to the point of uselessness for a backfill, so a real key belongs
    in SSM - but defaulting to it means this module can be exercised without
    one.
    """
    return os.environ.get("GUARDIAN_API_KEY") or "test"


def _get(params, cache_dir=None):
    query = urllib.parse.urlencode(params)

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9]+", "_", query)[:120] + ".json"
        local = os.path.join(cache_dir, name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            with open(local) as f:
                return json.load(f)

    req = urllib.request.Request(f"{BASE}?{query}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise SourceError(
                "rate limited - a real API key is needed for a backfill") from e
        raise SourceError(f"guardian request failed: {e}") from e
    except Exception as e:
        raise SourceError(f"guardian request failed: {e}") from e

    if cache_dir:
        with open(local, "w") as f:
            json.dump(payload, f)
    time.sleep(0.3)
    return payload


def is_routine(headline):
    """Coverage that is published every week is not an event."""
    return bool(ROUTINE.search(headline or ""))


def resolve_event_date(published, text):
    """
    The date the thing happened, or None.

    Publication date is not event date, and the gap is systematic rather than
    occasional: match reports run the morning after. Where the text says
    "on Tuesday" or "last night" the offset is knowable; where it says nothing,
    this returns None and the candidate is dropped.

    Returning None is the important branch. The alternative - assume the day
    before - would be right often enough to look fine and wrong often enough
    to put events on days they did not happen.
    """
    if not published or len(published) < 10:
        return None
    try:
        pub = date.fromisoformat(published[:10])
    except ValueError:
        return None

    lowered = (text or "").lower()

    if re.search(r"\b(last night|on friday night|yesterday)\b", lowered):
        return pub - timedelta(days=1)
    if re.search(r"\b(today|this afternoon|this evening|tonight)\b", lowered):
        return pub

    weekdays = ["monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"]
    for index, day_name in enumerate(weekdays):
        if re.search(rf"\bon {day_name}\b", lowered):
            delta = (pub.weekday() - index) % 7
            if delta == 0:
                # The named day is the day of publication, which is genuinely
                # ambiguous: print copy tends to mean a week ago, online copy
                # updated through the day routinely means this morning. This
                # used to assume a week back, and 23% of a sample week landed
                # there - a systematic error that puts a question on the wrong
                # calendar date, which is the one thing this cannot survive.
                #
                # So it declines, which is what the rest of this function does
                # whenever the text does not actually say.
                return None
            return pub - timedelta(days=delta)

    return None


def fetch_day(when, cache_dir=None, page_size=50):
    """
    Sport articles published on one date.

    Returns candidates, not events: every row still needs a resolvable event
    date and a human deciding whether it is worth a question.
    """
    if when.year < EARLIEST_YEAR:
        return []

    payload = _get({
        "from-date": when.isoformat(),
        "to-date": when.isoformat(),
        "section": "|".join(SPORT_SECTIONS),
        "show-fields": "headline,standfirst,trailText,body",
        "page-size": page_size,
        "api-key": _key(),
    }, cache_dir)

    response = payload.get("response") or {}
    if response.get("status") != "ok":
        raise SourceError(f"guardian returned {response.get('status')}")

    out = []
    for row in response.get("results") or []:
        fields = row.get("fields") or {}
        headline = fields.get("headline") or row.get("webTitle") or ""
        if not headline or is_routine(headline):
            continue

        summary = (fields.get("standfirst") or fields.get("trailText") or "")
        summary = re.sub(r"<[^>]+>", "", summary).strip()

        # The body is fetched, read for a date reference, and then dropped.
        #
        # Headline and standfirst almost never say when the thing happened -
        # measured at zero of forty-nine on a sample day - while the opening
        # paragraphs usually do, which lifts the resolvable share to a little
        # under half. The text itself is deliberately not kept: a link and a
        # short quote are defensible, an archive of somebody else's journalism
        # is not.
        published = row.get("webPublicationDate") or ""
        body = re.sub(r"<[^>]+>", " ", fields.get("body") or "")[:2000]
        event_date = resolve_event_date(published, f"{headline} {summary} {body}")

        out.append({
            "headline": headline.strip(),
            "summary": summary,
            "publishedAt": published[:10],
            "eventDate": event_date.isoformat() if event_date else None,
            "url": row.get("webUrl"),
            "section": row.get("sectionName"),
            "sourceName": SOURCE_NAME,
            "sourceDatasetRef": row.get("webUrl"),
        })
    return out


def fetch_range(start, end, cache_dir=None):
    """Every candidate across a span of dates."""
    out = []
    current = start
    while current <= end:
        try:
            out.extend(fetch_day(current, cache_dir))
        except SourceError as exc:
            log.warning(f"{current}: {exc}")
        current += timedelta(days=1)
    return out
