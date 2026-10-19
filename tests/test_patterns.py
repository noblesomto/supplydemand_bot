from core.candles import Candle
from core.patterns import (
    is_bullish_pin_bar, is_bearish_pin_bar,
    is_bullish_engulfing, is_bearish_engulfing,
    is_inside_bar, bullish_confirmation, bearish_confirmation,
)


def mk(o, h, l, c, ts="2024-01-01 00:00:00"):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def test_bullish_pin_bar():
    c = mk(1.10, 1.105, 1.080, 1.102)  # long lower wick, close near top
    assert is_bullish_pin_bar(c)
    assert not is_bearish_pin_bar(c)


def test_bearish_pin_bar():
    c = mk(1.10, 1.130, 1.098, 1.101)  # long upper wick, close near bottom
    assert is_bearish_pin_bar(c)
    assert not is_bullish_pin_bar(c)


def test_doji_is_not_a_pin_bar():
    c = mk(1.10, 1.102, 1.098, 1.1001)
    assert not is_bullish_pin_bar(c)
    assert not is_bearish_pin_bar(c)


def test_bullish_engulfing_single_bar():
    prior = mk(1.10, 1.102, 1.090, 1.092)  # bearish
    current = mk(1.091, 1.110, 1.089, 1.108)  # bullish, engulfs prior body
    assert is_bullish_engulfing([prior, current])
    assert not is_bearish_engulfing([prior, current])


def test_bearish_engulfing_single_bar():
    prior = mk(1.090, 1.102, 1.088, 1.100)  # bullish
    current = mk(1.101, 1.103, 1.085, 1.087)  # bearish, engulfs prior body
    assert is_bearish_engulfing([prior, current])


def test_bullish_engulfing_requires_net_bearish_prior_group():
    prior1 = mk(1.080, 1.095, 1.078, 1.090)  # bullish
    prior2 = mk(1.090, 1.100, 1.088, 1.095)  # bullish
    current = mk(1.085, 1.110, 1.083, 1.105)
    assert not is_bullish_engulfing([prior1, prior2, current], lookback=2)


def test_bullish_three_bar_engulfing():
    prior1 = mk(1.100, 1.102, 1.090, 1.095)
    prior2 = mk(1.095, 1.097, 1.085, 1.088)
    prior3 = mk(1.088, 1.090, 1.080, 1.082)
    current = mk(1.081, 1.105, 1.079, 1.103)
    assert is_bullish_engulfing([prior1, prior2, prior3, current], lookback=3)


def test_inside_bar():
    prev = mk(1.10, 1.120, 1.090, 1.105)
    cur = mk(1.10, 1.115, 1.095, 1.102)
    assert is_inside_bar(cur, prev)
    assert not is_inside_bar(prev, cur)


def test_bullish_confirmation_detects_pin_bar():
    assert bullish_confirmation([mk(1.10, 1.105, 1.080, 1.102)])


def test_bearish_confirmation_false_on_bullish_candle():
    assert not bearish_confirmation([mk(1.10, 1.105, 1.080, 1.102)])
