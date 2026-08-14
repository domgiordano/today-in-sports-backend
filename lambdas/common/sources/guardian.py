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
    r"what to watch|the fiver|rumours?|gossip|squad sheets?|"
    r"how they stand|in numbers|talking horses|racing tips?|sums? up)\b", re.I)

# Not an event, whatever the headline says.
#
# A trailing "- report" marks a story the paper is not standing behind, and
# "Mourinho has signed pre-contract agreement with Manchester United - report"
# scored highest of anything on a sampled day while not having happened. Video
# and podcast items are a format, not an occurrence.
#
# Kept out of the ROUTINE pattern because these are anchored to punctuation and
# line endings rather than word boundaries, and \b cannot precede a dash - which
# is exactly why the first attempt at this matched nothing at all.
UNCONFIRMED = re.compile(
    r"[-–—]\s*(report|reports)\s*$|\bvideo\b|\bpodcast\b|paper review", re.I)


# Somebody talking about a thing that has not happened yet.
#
# The routine filter above catches formats - a preview, a live blog. This
# catches the far larger category the archive is actually full of: a manager
# saying something before a game. Of eighteen headlines sampled from two March
# days, three were events and the rest were "Gatland prepares Wales to run at
# Ireland", "Johnson warns England they must seize high ground", "Davies seeks
# FA Cup glory". None of those is a thing that happened on a date.
QUOTED = re.compile(
    r"\b(says?|said|insists?|warns?|seeks?|prepares?|hopes?|urges?|backs?|"
    r"admits?|denies|claims?|believes?|expects?|calls? for|wants?|vows?|"
    r"targets?|eyes?|braced|relish(es)?|faces?|must|should|could|will)\b",
    re.I)

# The event classes a date-anchored quiz can actually use: a thing that
# happened to somebody, on a day. Drawn from what the archive yields rather
# than invented - transfers, sackings, retirements, bans and injuries are the
# off-season's entire supply of news.
NEWSWORTHY = (
    # Both voices and both vocabularies: a British paper writes "Rovers sack
    # manager", an American one "Nets fire coach", and the first version of
    # this had only the past participle "sacked" - so an active-voice sacking,
    # which is how most of them are written, scored zero.
    (re.compile(r"\b(sacks?|sacked|fires?|fired|dismissed|resigns?|"
                r"steps? down|departs?|"
                r"appointed|named (as )?(the )?(new )?(head )?(coach|manager)|"
                r"takes? charge)\b", re.I), 30),
    (re.compile(r"\b(retires?|retirement|quits?|calls? time|"
                r"announces? his retirement)\b", re.I), 30),
    (re.compile(r"\b(banned|ban|charged|suspended|doping|drug test|"
                r"investigation|arrested|guilty|cleared of|fined|"
                r"stripped of|scandal|charges?|allegations?|inquiry)\b", re.I), 28),
    (re.compile(r"\b(signs?|joins?|completes? (a )?(move|transfer)|"
                r"transfer|sold to|agrees? (a )?deal|sealed? a move)\b",
                re.I), 22),
    (re.compile(r"\b(record|first ever|fastest|youngest|oldest|"
                r"breaks? the|becomes? the first)\b", re.I), 20),
    (re.compile(r"\b(injured|injury|ruled out|out for the season|"
                r"surgery|torn|fractured)\b", re.I), 16),
    (re.compile(r"\b(wins?|won|beat|beats|defeats?|clinch(es|ed)?|"
                r"champions?|title)\b", re.I), 8),
)

# The bar to reach the queue at all.
#
# Deliberately low, and set to the weakest event class rather than to something
# selective. This score sorts a queue a person reads top-down and abandons when
# they have had enough - so a long list ranked well beats a short one that
# quietly dropped the best story. Tuned the other way, the first version cut 87
# articles to 5 and threw away "No charges over Ashley Cole air rifle incident
# at Chelsea", which is exactly the kind of thing this source exists for.
#
# Over-filtering loses events silently. Under-filtering only costs scrolling.
MIN_CANDIDATE_SCORE = 8


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


def candidate_score(headline, summary=""):
    """
    How likely this article is to describe a thing that happened.

    The archive is mostly people talking about sport rather than sport
    happening, and a queue somebody has to work by hand cannot afford that
    ratio. Scoring lets the crawl stay broad while the queue stays short.

    Returns 0 for anything quoting somebody ahead of an event, and otherwise
    the strongest event class the text matches - strongest rather than summed,
    so a sacking mentioned alongside a transfer is scored as one sacking and
    not as unusually important.
    """
    text = f"{headline or ''} {summary or ''}"
    if not text.strip():
        return 0
    if QUOTED.search(headline or "") or UNCONFIRMED.search(headline or ""):
        return 0
    return max((points for pattern, points in NEWSWORTHY
                if pattern.search(text)), default=0)


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

        score = candidate_score(headline, summary)
        if score < MIN_CANDIDATE_SCORE:
            continue

        out.append({
            "candidateScore": score,
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
