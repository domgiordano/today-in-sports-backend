import json
import os

import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_games(date):
    """Recorded schedule response for a date. Tests never hit the network."""
    with open(os.path.join(FIXTURES, f"mlb_{date}.json")) as f:
        return json.load(f)["games"]


@pytest.fixture
def games():
    return load_games


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """
    Hard-fail any test that tries to reach the MLB API.

    The boxscore enrichment path is the only thing that would, and every test
    that needs it patches it explicitly. Anything else reaching the network is a
    bug in the test, not a slow test.
    """
    from lambdas.common.sources import mlb

    def _blocked(*_a, **_kw):
        raise AssertionError("test attempted a live HTTP call")

    monkeypatch.setattr(mlb, "_get", _blocked)
