"""
Request and error logging.

The governing property is that this code must never raise. It runs inside the
error handler's `finally`, on every request, and an exception escaping it would
turn a logging problem into a failed response - strictly worse than no logging.

That is also how this module came to be missing entirely: the hook imported it
inside a try/except that swallowed the ImportError, so every write silently did
nothing and nobody found out.
"""

from unittest.mock import MagicMock, patch

import pytest

from lambdas.common import request_log_dynamo as rl


@pytest.fixture
def table():
    t = MagicMock()
    with patch.object(rl, "_table", return_value=t):
        yield t


# --------------------------------------------------------------- bucketing

@pytest.mark.parametrize("status,error,expected", [
    (200, None, "ok"),
    (201, None, "ok"),
    (400, None, "rejected"),
    (404, None, "rejected"),
    (500, None, "error"),
    (502, None, "error"),
    # An error message with a 200 is still an error: the handler recovered
    # enough to respond but something went wrong worth keeping.
    (200, "boom", "error"),
])
def test_outcome_bucketing(status, error, expected):
    assert rl._bucket(status, error) == expected


def test_errors_are_kept_far_longer_than_successes(table):
    """A 500 is looked up a fortnight later; a 200 is only useful in aggregate."""
    rl.record_request("/play/start", "POST", 500)
    err_ttl = table.put_item.call_args[1]["Item"]["expiresAt"]

    table.reset_mock()
    rl.record_request("/play/start", "POST", 200)
    ok_ttl = table.put_item.call_args[1]["Item"]["expiresAt"]

    assert err_ttl > ok_ttl


# ------------------------------------------------------------------ writing

def test_a_request_is_recorded_with_its_outcome(table):
    rl.record_request("/play/answer", "POST", 500, email="a@b.com",
                      duration_ms=12, error="kaboom")

    item = table.put_item.call_args[1]["Item"]
    assert item["bucket"] == "error"
    assert item["path"] == "/play/answer"
    assert item["method"] == "POST"
    assert item["status"] == 500
    assert item["email"] == "a@b.com"
    assert item["durationMs"] == 12
    assert item["error"] == "kaboom"


def test_a_long_error_is_truncated(table):
    """A stack trace is unreadable in a table and is already in CloudWatch."""
    rl.record_request("/x", "GET", 500, error="y" * 5000)
    assert len(table.put_item.call_args[1]["Item"]["error"]) == 500


def test_anonymous_callers_do_not_get_an_empty_email_field(table):
    rl.record_request("/play/start", "POST", 200)
    assert "email" not in table.put_item.call_args[1]["Item"]


# ---------------------------------------------------------- never raising

def test_a_write_failure_is_swallowed(table):
    """
    The whole reason this module is best-effort. A logging outage must not
    become an API outage.
    """
    table.put_item.side_effect = RuntimeError("dynamo is down")
    rl.record_request("/play/start", "POST", 200)  # must not raise


def test_a_last_seen_failure_is_swallowed(table):
    table.update_item.side_effect = RuntimeError("dynamo is down")
    rl.upsert_last_seen("a@b.com")  # must not raise


def test_unserialisable_input_does_not_raise(table):
    rl.record_request(None, None, None)  # must not raise


# ------------------------------------------------------------------ reading

def test_recent_reads_newest_first(table):
    table.query.return_value = {"Items": [{"bucket": "error"}]}
    rows = rl.recent("error", 10)

    assert rows == [{"bucket": "error"}]
    assert table.query.call_args[1]["ScanIndexForward"] is False


def test_the_read_limit_is_capped(table):
    table.query.return_value = {"Items": []}
    rl.recent("error", 99999)
    assert table.query.call_args[1]["Limit"] <= 500
