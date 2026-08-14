

def test_both_score_shapes_are_read():
    """
    openfootball carries two shapes in the same file: most matches use
    {"ft": [4, 2]}, some a bare [0, 0]. In the 2025-26 Premier League export
    that is 27 of 380 - and the bare form raised AttributeError, which killed
    the league, which killed the season, which is why recent soccer was empty.
    """
    from lambdas.common.sources import football_json as fj

    assert fj._full_time({"score": {"ft": [4, 2], "ht": [1, 0]}}) == (4, 2)
    assert fj._full_time({"score": [0, 0]}) == (0, 0)
    assert fj._full_time({"score": None}) is None
    assert fj._full_time({"score": [1]}) is None
    assert fj._full_time({}) is None
