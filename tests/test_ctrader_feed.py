from types import SimpleNamespace

import pandas as pd

from core.feeds.ctrader import decode_trendbars


def _bar(minute_ts: int, low: int, d_open: int, d_high: int, d_close: int, vol: int = 5):
    return SimpleNamespace(utcTimestampInMinutes=minute_ts, low=low, deltaOpen=d_open,
                           deltaHigh=d_high, deltaClose=d_close, volume=vol)


def test_decode_trendbars_low_plus_deltas():
    t = int(pd.Timestamp("2025-03-04 10:00", tz="UTC").timestamp() // 60)
    df = decode_trendbars([_bar(t + 1, 290000000, 50000, 120000, 80000), _bar(t, 289900000, 0, 10000, 5000)])
    assert list(df.index) == [pd.Timestamp("2025-03-04 10:00", tz="UTC"), pd.Timestamp("2025-03-04 10:01", tz="UTC")]
    r = df.iloc[1]
    assert (r.bid_open, r.bid_high, r.bid_low, r.bid_close, r.volume) == (2900.5, 2901.2, 2900.0, 2900.8, 5.0)
    assert decode_trendbars([]).empty
