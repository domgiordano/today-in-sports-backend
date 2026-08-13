"""
Request and error log.

`errors.py` has called into this module on every request since the error
handling was ported, inside a try/except that swallows anything it raises so
instrumentation can never break a response. The module did not exist, so the
hook has been quietly doing nothing - which is exactly the failure mode that
design invites, and the reason there is no operational view of the API today.

Rows are written for every request and expire on their own. Errors are kept far
longer than successes: a 200 is only useful in aggregate and for a short window,
while a 500 is worth having when somebody reports a problem a fortnight later.

Nothing here may raise. A logging failure that broke a request would be a
strictly worse outcome than no logging at all.
"""

import time
from datetime import datetime, timezone

import boto3

from lambdas.common import constants
from lambdas.common.logger import get_logger

log = get_logger(__file__)

_dynamo = None

# Successful requests are bulk and only interesting in aggregate.
SUCCESS_TTL_DAYS = 14
# Failures outlive them, because they are looked up after the fact.
ERROR_TTL_DAYS = 90


def _table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb")
    return _dynamo.Table(constants.REQUEST_LOG_TABLE_NAME)


def _now():
    return datetime.now(timezone.utc)


def _bucket(status, error):
    """
    Coarse outcome, which is what an operator filters on.

    Status decides first, and deliberately so. Every handled 4xx carries an
    error message, so treating any message as a failure put "no published quiz
    for today" - an expected, correct 404 - in the same bucket as a genuine
    500, made the rejected bucket unreachable, and would have buried real
    faults under routine ones.

    A message alongside a 2xx is still a failure: the handler recovered enough
    to answer, but something went wrong worth keeping.
    """
    status = int(status or 0)
    if status >= 500:
        return "error"
    if status >= 400:
        return "rejected"
    if error:
        return "error"
    return "ok"


def record_request(path, method, status, email="", duration_ms=None,
                   error=None):
    """
    Persist one request. Best-effort, and silent on failure by design.

    The partition key is the outcome bucket rather than the path, so the errors
    panel is a query rather than a scan - the whole point is to answer "what is
    broken" without reading every successful request ever served.
    """
    try:
        now = _now()
        bucket = _bucket(int(status or 0), error)
        ttl_days = ERROR_TTL_DAYS if bucket == "error" else SUCCESS_TTL_DAYS

        item = {
            "bucket": bucket,
            "loggedAt": f"{now.isoformat()}#{time.time_ns()}",
            "path": str(path or "unknown"),
            "method": str(method or "unknown"),
            "status": int(status or 0),
            "expiresAt": int(now.timestamp()) + ttl_days * 86400,
        }
        if email:
            item["email"] = email
        if duration_ms is not None:
            item["durationMs"] = int(duration_ms)
        if error:
            # Truncated: a stack trace in a log row is unreadable in a table
            # and the full trace is already in CloudWatch.
            item["error"] = str(error)[:500]

        _table().put_item(Item=item)
    except Exception as err:  # noqa: BLE001 - instrumentation must never raise
        log.warning(f"request-log write failed (ignored): {err}")


def upsert_last_seen(email):
    """
    Stamp when an identified caller was last active.

    Deliberately on the same table rather than a new one: this is the same
    instrumentation concern, and it is written from the same swallow-everything
    hook. The user record proper is a separate thing with a separate lifecycle.
    """
    try:
        _table().update_item(
            Key={"bucket": "last-seen", "loggedAt": email},
            UpdateExpression="SET lastSeenAt = :t ADD requestCount :one",
            ExpressionAttributeValues={":t": _now().isoformat(), ":one": 1},
        )
    except Exception as err:  # noqa: BLE001
        log.warning(f"last-seen write failed (ignored): {err}")


def recent(bucket="error", limit=100):
    """Most recent rows in a bucket, newest first."""
    from boto3.dynamodb.conditions import Key

    resp = _table().query(
        KeyConditionExpression=Key("bucket").eq(bucket),
        ScanIndexForward=False,
        Limit=min(int(limit), 500),
    )
    return resp.get("Items", [])


def active_callers(limit=200):
    """Identified callers, most recently seen first."""
    rows = recent("last-seen", limit)
    return sorted(rows, key=lambda r: r.get("lastSeenAt", ""), reverse=True)
