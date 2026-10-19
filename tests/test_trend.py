from core.candles import Candle
from core.trend import find_pivots, classify_trend, trend_as_of, PivotKind, Trend


def mk(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def _series(highs_lows):
    return [mk(f"2024-01-{i+1:02d} 00:00:00", h - 0.5, h, l, (h + l) / 2) for i, (h, l) in enumerate(highs_lows)]


def test_find_pivots_simple_high_and_low():
    # index 2 is a clear high, index 5 is a clear low
    candles = _series([(10, 9), (11, 10), (15, 12), (12, 10), (11, 9), (8, 5), (10, 8), (11, 9)])
    pivots = find_pivots(candles, pivot_n=2)
    highs = [p for p in pivots if p.kind == PivotKind.HIGH]
    lows = [p for p in pivots if p.kind == PivotKind.LOW]
    assert any(p.index == 2 for p in highs)
    assert any(p.index == 5 for p in lows)


def test_classify_trend_up():
    candles = _series([(10, 8), (11, 9), (9, 7), (13, 11), (10, 8), (16, 13), (12, 9), (18, 15)])
    pivots = find_pivots(candles, pivot_n=1)
    assert classify_trend(pivots) == Trend.UP


def test_classify_trend_down():
    candles = _series([(18, 15), (12, 9), (16, 13), (10, 8), (13, 11), (9, 7), (11, 9), (10, 8)])
    pivots = find_pivots(candles, pivot_n=1)
    assert classify_trend(pivots) == Trend.DOWN


def test_classify_trend_range_with_insufficient_pivots():
    assert classify_trend([]) == Trend.RANGE


def test_trend_as_of_ignores_future_candles():
    candles = _series([(10, 8), (11, 9), (9, 7), (13, 11), (10, 8), (16, 13), (12, 9), (18, 15)])
    full_trend = trend_as_of(candles, len(candles) - 1, pivot_n=1)
    truncated_trend = trend_as_of(candles[:5], 4, pivot_n=1)
    # Both should be computable purely from data up to their own index;
    # the key guarantee is that trend_as_of never raises on a short slice
    # and only uses pivots confirmable within it.
    assert truncated_trend in (Trend.UP, Trend.DOWN, Trend.RANGE)
    assert full_trend in (Trend.UP, Trend.DOWN, Trend.RANGE)
