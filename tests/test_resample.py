from core.candles import Candle
from core.resample import resample_candles, resample_to_d1


def h4(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def test_resample_h4_to_d1_groups_sunday_stub_with_monday():
    candles = [
        h4("2024-01-07 22:00:00", 1.000, 1.002, 0.999, 1.001),  # Sunday open stub
        h4("2024-01-08 02:00:00", 1.001, 1.010, 1.000, 1.008),  # rest of the Monday trading day
        h4("2024-01-08 06:00:00", 1.008, 1.012, 1.005, 1.009),
        h4("2024-01-08 10:00:00", 1.009, 1.015, 1.008, 1.012),
        h4("2024-01-08 14:00:00", 1.012, 1.016, 1.010, 1.013),
        h4("2024-01-08 18:00:00", 1.013, 1.018, 1.012, 1.014),
        h4("2024-01-08 22:00:00", 1.014, 1.020, 1.013, 1.018),  # first bar of the Tuesday trading day
        h4("2024-01-09 02:00:00", 1.018, 1.022, 1.017, 1.020),
    ]
    d1 = resample_to_d1(candles)
    assert len(d1) == 2
    monday = d1[0]
    assert monday.timestamp == "2024-01-07 22:00:00"  # keeps the Sunday stub's open timestamp
    assert monday.open == 1.000  # Sunday's open carries through as the day's open
    assert monday.high == 1.018
    assert monday.low == 0.999
    assert monday.close == 1.014  # last bar still inside the Monday trading day (before 22:00)
    tuesday = d1[1]
    assert tuesday.timestamp == "2024-01-08 22:00:00"
    assert tuesday.high == 1.022
    assert tuesday.low == 1.013


def test_resample_m15_to_h1_keeps_only_full_buckets():
    candles = [
        h4("2024-01-01 00:00:00", 1.0, 1.01, 0.99, 1.005),
        h4("2024-01-01 00:15:00", 1.005, 1.02, 1.00, 1.015),
        h4("2024-01-01 00:30:00", 1.015, 1.03, 1.01, 1.025),
        h4("2024-01-01 00:45:00", 1.025, 1.04, 1.02, 1.035),
        h4("2024-01-01 01:00:00", 1.035, 1.05, 1.03, 1.045),  # partial next bucket, dropped
    ]
    h1 = resample_candles(candles, "h1")
    assert len(h1) == 1
    assert h1[0].open == 1.0
    assert h1[0].high == 1.04
    assert h1[0].low == 0.99
    assert h1[0].close == 1.035


def test_resample_m1_to_m15_keeps_only_full_buckets():
    candles = [h4(f"2024-01-01 00:{m:02d}:00", 1.0 + m * 0.001, 1.01, 0.99, 1.0 + m * 0.001) for m in range(15)]
    candles.append(h4("2024-01-01 00:15:00", 1.02, 1.03, 1.01, 1.025))  # partial next bucket, dropped
    m15 = resample_candles(candles, "m15")
    assert len(m15) == 1
    assert m15[0].open == candles[0].open
    assert m15[0].close == candles[14].close
    assert m15[0].high == 1.01


def test_resample_empty():
    assert resample_candles([], "h4") == []
    assert resample_to_d1([]) == []
