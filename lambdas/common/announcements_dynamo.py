"""
Announcements.

A way to tell everyone something changed, shown as a modal on next load.

Two rules learned from every product that has done this badly, and both are
enforced here rather than left to whoever writes the announcement:

  * **An announcement must end.** Without a hard end date it runs forever after
    you forget about it, and a stale banner is worse than no banner because it
    teaches people to ignore the channel.
  * **It must never cover the quiz.** Announcements surface on the landing page
    and the results screen only. Interrupting someone mid-question to tell them
    about a new feature is the fastest way to make both feel worse.

Dismissal is per user and stored client-side. That is a deliberate limit: a
dismissal that follows you across devices needs the user record, and an
announcement is not worth a write on every page load.
"""

import uuid
from datetime import datetime, timedelta, timezone

import boto3

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

# Where an announcement may appear. Never mid-quiz.
VALID_PLACEMENTS = ("landing", "results")

SEVERITIES = ("info", "notice", "warning")

# How long an announcement runs when nobody says otherwise.
DEFAULT_RUN_DAYS = 14
MAX_RUN_DAYS = 90

_dynamo = None


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.ANNOUNCEMENTS_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc)


def create(title, body, severity="info", placements=None, run_days=None,
           dismissible=True):
    title = (title or "").strip()
    if not title:
        raise ValueError("an announcement needs a title")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}")

    placements = [p for p in (placements or VALID_PLACEMENTS)
                  if p in VALID_PLACEMENTS]
    if not placements:
        raise ValueError("an announcement needs somewhere to appear")

    days = min(int(run_days or DEFAULT_RUN_DAYS), MAX_RUN_DAYS)
    now = _now()
    ends = now + timedelta(days=days)

    item = {
        "announcementId": str(uuid.uuid4()),
        "title": title[:120],
        "body": (body or "").strip()[:600],
        "severity": severity,
        "placements": placements,
        "startsAt": now.isoformat(),
        "endsAt": ends.isoformat(),
        "dismissible": bool(dismissible),
        "createdAt": now.isoformat(),
        # Expired rows clean themselves up rather than accumulating forever.
        "expiresAt": int(ends.timestamp()) + 30 * 86400,
    }
    _table().put_item(Item=item)
    log.info(f"announcement {item['announcementId']} runs until {ends.date()}")
    return item


def list_all(limit=100):
    resp = _table().scan(Limit=min(int(limit), 200))
    return sorted(resp.get("Items", []),
                  key=lambda a: a.get("createdAt", ""), reverse=True)


def active(placement=None, at=None):
    """
    Announcements currently running, optionally for one placement.

    The window is checked here rather than trusted from the row, so an
    announcement whose end date has passed simply stops appearing without
    anybody having to remember to take it down.
    """
    moment = (at or _now()).isoformat()
    out = []
    for row in list_all(200):
        if row.get("startsAt", "") > moment:
            continue
        if row.get("endsAt", "") <= moment:
            continue
        if placement and placement not in (row.get("placements") or []):
            continue
        out.append(row)
    return out


def end_now(announcement_id):
    """Take one down immediately, without deleting the record."""
    resp = _table().update_item(
        Key={"announcementId": announcement_id},
        UpdateExpression="SET endsAt = :now",
        ExpressionAttributeValues={":now": _now().isoformat()},
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes")


def public_view(row):
    return {
        "announcementId": row.get("announcementId"),
        "title": row.get("title"),
        "body": row.get("body"),
        "severity": row.get("severity", "info"),
        "dismissible": bool(row.get("dismissible", True)),
        "endsAt": row.get("endsAt"),
    }
