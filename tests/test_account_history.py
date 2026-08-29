"""
Your own record.

The public numbers say how everybody does; this is the half a player came for.
The tests that matter are about what the window refuses to invent — a day
nobody played is not a zero, and an answer with no sport recorded is not a
sport.
"""

from datetime import date

import pytest

from lambdas.account_history import handler as h


def session(day, points, correct, answers=None, complete=True):
    s = {
        'quizDate': day,
        'totalPoints': points,
        'correctCount': correct,
        'questionIds': ['q1', 'q2', 'q3', 'q4', 'q5'],
        'answers': answers if answers is not None else [
            {'correct': i < correct, 'sport': 'nfl', 'seconds': '4'}
            for i in range(5)
        ],
    }
    if complete:
        s['completedAt'] = f'{day}T12:00:00+00:00'
    return s


@pytest.fixture
def api(monkeypatch):
    def run(sessions, params=None):
        monkeypatch.setattr(h.plays_dynamo, 'history',
                            lambda uid, days, today: sessions)
        monkeypatch.setattr(h.group_access, 'caller', lambda e, hh: 'u1')
        monkeypatch.setattr(h, 'today_utc', lambda: '2026-08-28')
        event = {'queryStringParameters': params or {}}
        import json
        return json.loads(h.handler(event, None)['body'])
    return run


class TestTheWindow:
    def test_reports_each_round_that_was_finished(self, api):
        out = api([session('2026-08-28', 38, 3), session('2026-08-27', 45, 4)])
        assert [r['quizDate'] for r in out['rounds']] == ['2026-08-28', '2026-08-27']
        assert out['window']['roundsPlayed'] == 2
        assert out['window']['bestPoints'] == 45

    def test_a_day_not_played_is_absent_rather_than_a_zero(self, api):
        # The whole reason history() filters rather than pads: a caller that
        # cannot tell "did not play" from "scored nothing" will draw the second.
        out = api([session('2026-08-28', 38, 3)])
        assert len(out['rounds']) == 1
        assert out['window']['avgPoints'] == 38

    def test_an_empty_window_averages_to_zero_without_dividing_by_it(self, api):
        out = api([])
        assert out['rounds'] == []
        assert out['window'] == {
            'roundsPlayed': 0, 'avgPoints': 0, 'avgCorrect': 0,
            'bestPoints': 0, 'perfectRounds': 0,
        }

    def test_counts_a_perfect_round(self, api):
        out = api([session('2026-08-28', 50, 5), session('2026-08-27', 10, 1)])
        assert out['window']['perfectRounds'] == 1

    def test_totals_the_time_taken_rather_than_averaging_it(self, api):
        # A player comparing two days wants "did I take longer today", which a
        # mean of five answers hides.
        out = api([session('2026-08-28', 38, 3)])
        assert out['rounds'][0]['seconds'] == 20

    def test_survives_a_round_with_no_timings(self, api):
        answers = [{'correct': True, 'sport': 'nfl'}] * 5
        out = api([session('2026-08-28', 38, 3, answers=answers)])
        assert out['rounds'][0]['seconds'] is None


class TestBySport:
    def test_accuracy_per_sport(self, api):
        answers = [
            {'correct': True, 'sport': 'nfl'}, {'correct': False, 'sport': 'nfl'},
            {'correct': True, 'sport': 'nba'},
        ]
        out = api([session('2026-08-28', 30, 2, answers=answers)])
        assert out['bySport']['nfl'] == {'asked': 2, 'correct': 1, 'accuracy': 0.5}
        assert out['bySport']['nba']['accuracy'] == 1.0

    def test_an_answer_with_no_sport_recorded_invents_none(self, api):
        # Bucketing these as "unknown" would put a sport nobody played on a
        # chart of what they know.
        answers = [{'correct': True}, {'correct': True, 'sport': 'nhl'}]
        out = api([session('2026-08-28', 20, 2, answers=answers)])
        assert list(out['bySport']) == ['nhl']
        assert out['bySport']['nhl']['asked'] == 1

    def test_accumulates_across_days(self, api):
        one = [{'correct': True, 'sport': 'mlb'}]
        two = [{'correct': False, 'sport': 'mlb'}]
        out = api([session('2026-08-28', 10, 1, answers=one),
                   session('2026-08-27', 0, 0, answers=two)])
        assert out['bySport']['mlb'] == {'asked': 2, 'correct': 1, 'accuracy': 0.5}


class TestTheWindowSize:
    @pytest.mark.parametrize('given,expected', [
        (None, 30), ('7', 7), ('0', 1), ('-5', 1), ('9999', 90), ('abc', 30),
    ])
    def test_days_is_clamped_to_something_answerable(self, api, given, expected):
        out = api([], {'days': given} if given is not None else {})
        assert out['days'] == expected


class TestHistoryReads:
    """
    The BatchGet itself. No index and no scan: playId is `identity#quizDate`,
    so every key in the window is computable.
    """

    def _plays(self, monkeypatch, responses):
        from lambdas.common import plays_dynamo as pd
        calls = []

        class Table:
            name = 'plays'

        class Resource:
            def batch_get_item(self, RequestItems):
                calls.append(RequestItems)
                return responses.pop(0)

        monkeypatch.setattr(pd, '_table', lambda: Table())
        monkeypatch.setattr(pd, '_resource', lambda: Resource())
        return pd, calls

    def test_asks_for_exactly_the_days_wanted(self, monkeypatch):
        pd, calls = self._plays(monkeypatch, [{'Responses': {'plays': []}}])
        pd.history('u1', 3, date(2026, 8, 28))

        keys = [k['playId'] for k in calls[0]['plays']['Keys']]
        assert keys == ['u1#2026-08-28', 'u1#2026-08-27', 'u1#2026-08-26']

    def test_returns_finished_rounds_newest_first(self, monkeypatch):
        rows = [
            {'quizDate': '2026-08-26', 'completedAt': 'x'},
            {'quizDate': '2026-08-28', 'completedAt': 'x'},
        ]
        pd, _ = self._plays(monkeypatch, [{'Responses': {'plays': rows}}])
        out = pd.history('u1', 3, date(2026, 8, 28))
        assert [r['quizDate'] for r in out] == ['2026-08-28', '2026-08-26']

    def test_an_abandoned_round_is_not_history(self, monkeypatch):
        rows = [{'quizDate': '2026-08-28'}, {'quizDate': '2026-08-27', 'completedAt': 'x'}]
        pd, _ = self._plays(monkeypatch, [{'Responses': {'plays': rows}}])
        out = pd.history('u1', 3, date(2026, 8, 28))
        assert [r['quizDate'] for r in out] == ['2026-08-27']

    def test_a_long_window_is_chunked_under_the_batch_limit(self, monkeypatch):
        pd, calls = self._plays(monkeypatch, [
            {'Responses': {'plays': []}}, {'Responses': {'plays': []}}])
        pd.history('u1', 120, date(2026, 8, 28))

        assert [len(c['plays']['Keys']) for c in calls] == [100, 20]

    def test_throttled_keys_are_retried_rather_than_dropped(self, monkeypatch):
        # Dynamo returns unprocessed keys under throttling instead of failing.
        # Ignoring them silently shortens somebody's history.
        held = {'plays': {'Keys': [{'playId': 'u1#2026-08-27'}]}}
        pd, calls = self._plays(monkeypatch, [
            {'Responses': {'plays': [{'quizDate': '2026-08-28', 'completedAt': 'x'}]},
             'UnprocessedKeys': held},
            {'Responses': {'plays': [{'quizDate': '2026-08-27', 'completedAt': 'x'}]}},
        ])
        out = pd.history('u1', 2, date(2026, 8, 28))

        assert len(calls) == 2
        assert [r['quizDate'] for r in out] == ['2026-08-28', '2026-08-27']
