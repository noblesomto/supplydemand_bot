"""Spec section 11: a setup/trade detected using data up to bar t must be
identical whether the engine is given data truncated at t or the full
series -- no information from bars after t may influence it."""

from datetime import datetime, timedelta

from core.candles import Candle
from core.backtest_engine import run_backtest
from rulebook.models import Rulebook, ZoneRules, SetupRules, RiskRules, ExitRules

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _d1(i, o, h, l, c):
    return Candle(timestamp=f"2024-01-{i+1:02d} 22:00:00", open=o, high=h, low=l, close=c)


def _h4_from_d1(d1_candle, n=6):
    """H4 sub-bars spanning [d1_candle.timestamp, d1_candle.timestamp + 24h)
    -- i.e. starting exactly at the D1 bar's own 22:00 timestamp and
    running into the next calendar date, matching the D1_H4 profile's
    actual day-boundary convention (core.resample.resample_to_d1)."""
    start = datetime.strptime(d1_candle.timestamp, _TS_FORMAT)
    bars = []
    step = (d1_candle.close - d1_candle.open) / n
    for k in range(n):
        o = d1_candle.open + step * k
        c = d1_candle.open + step * (k + 1)
        h = max(o, c) + (d1_candle.high - max(d1_candle.open, d1_candle.close)) / n
        l = min(o, c) - (min(d1_candle.open, d1_candle.close) - d1_candle.low) / n
        ts = (start + timedelta(hours=4 * k)).strftime(_TS_FORMAT)
        bars.append(Candle(timestamp=ts, open=o, high=h, low=l, close=c))
    return bars


def _rulebook():
    return Rulebook(
        zone=ZoneRules(max_base=3, base_body_atr=0.5, base_range_atr=1.5, leg_atr=1.0,
                        leg_out_bars=2, departure_atr=1.5, max_touches=3, break_atr=1.0,
                        break_bars=2, zone_max_age=100, atr_period=3),
        setup=SetupRules(trap_mode="wick", accepted_grades=["A"], confirm_window=5, h4_window=2),
        # round_targets=True gives this synthetic single-zone fixture an
        # opposing target to size RR against (no second, opposite-type
        # zone exists in this short series to serve as one otherwise).
        risk=RiskRules(risk_per_trade_pct=1.0, min_rr=0.01, sl_buffer_atr=0.1, round_targets=True),
        exit=ExitRules(model="next_level"),
    )


def _build_series():
    candles = [_d1(i, 1.0 + i * 0.0001, 1.002 + i * 0.0001, 0.998 + i * 0.0001, 1.0005 + i * 0.0001) for i in range(8)]
    candles += [
        _d1(8, 1.010, 1.011, 0.990, 0.991),
        _d1(9, 0.991, 0.993, 0.989, 0.992),
        _d1(10, 0.992, 0.994, 0.990, 0.991),
        _d1(11, 0.991, 1.015, 0.990, 1.014),
        _d1(12, 1.014, 1.040, 1.013, 1.039),
        _d1(13, 1.039, 1.041, 1.010, 1.015),
        _d1(14, 1.015, 1.016, 0.985, 0.987),
        _d1(15, 0.986, 0.988, 0.965, 0.987),  # bullish pin bar -> zone-TF (D1) confirmation
    ]
    # Padding bars strictly AFTER the trade resolves -- these exist only
    # so the "full series" run has real future data beyond the trade for
    # the truncated run to omit; their own values are unremarkable.
    candles += [_d1(16 + k, 1.003, 1.006, 1.001, 1.004) for k in range(4)]
    return candles


def _h4_for_day15(d1_candle):
    """Hand-built H4 bars for the zone-confirmation day's follow-through,
    starting exactly at the D1 bar's own timestamp (see _h4_from_d1): the
    generic linear splitter never produces an actual candlestick pattern,
    so the confirmation-TF entry scan needs an explicit bullish engulfing
    here to reach ENTRY at all."""
    start = datetime.strptime(d1_candle.timestamp, _TS_FORMAT)

    def ts(hours):
        return (start + timedelta(hours=hours)).strftime(_TS_FORMAT)

    return [
        Candle(timestamp=ts(0), open=0.986, high=0.988, low=0.983, close=0.985),   # bearish
        Candle(timestamp=ts(4), open=0.984, high=1.000, low=0.982, close=0.998),   # bullish engulfing
        Candle(timestamp=ts(8), open=0.998, high=1.002, low=0.996, close=1.000),
        Candle(timestamp=ts(12), open=1.000, high=1.003, low=0.998, close=1.001),
        Candle(timestamp=ts(16), open=1.001, high=1.004, low=0.999, close=1.002),
        Candle(timestamp=ts(20), open=1.002, high=1.005, low=1.000, close=1.003),
    ]


def test_trades_before_truncation_point_are_identical_with_or_without_future_bars():
    candles = _build_series()
    rulebook = _rulebook()

    day15_index = 15
    # The day-15 pin bar confirms only at day 15's close, so the entry
    # pattern lives in day 16's H4 bars (entry search starts at the close).
    full_h4 = (
        [h for d in candles[:day15_index + 1] for h in _h4_from_d1(d)]
        + _h4_for_day15(candles[day15_index + 1])
        + [h for d in candles[day15_index + 2:] for h in _h4_from_d1(d)]
    )
    full_trades, _ = run_backtest(
        pair="EUR/USD", zone_tf_candles=candles, confirm_tf_candles=full_h4,
        confirm_bars_per_zone_bar=6, rulebook=rulebook,
    )
    assert full_trades, "fixture must actually produce at least one trade to be a meaningful check"

    # Truncate right after day 15 (the entry day) -- the h4_window=2
    # confirmation scan needs zone bars 14 and 15 present; the 4 padding
    # days after that are real "future" data the truncated run omits.
    last_confirm_ts = max(t.zone_confirmed_timestamp for t in full_trades)
    confirm_idx = next(i for i, c in enumerate(candles) if c.timestamp == last_confirm_ts)
    cutoff = confirm_idx + 2
    assert cutoff < len(candles), "fixture must have real future bars beyond the trade to be a meaningful check"
    truncated = candles[:cutoff]
    truncated_h4 = (
        [h for d in truncated[:day15_index + 1] for h in _h4_from_d1(d)]
        + _h4_for_day15(truncated[day15_index + 1])
    )

    truncated_trades, _ = run_backtest(
        pair="EUR/USD", zone_tf_candles=truncated, confirm_tf_candles=truncated_h4,
        confirm_bars_per_zone_bar=6, rulebook=rulebook,
    )

    full_keys = {(t.entry_timestamp, t.direction, round(t.levels.entry, 6)) for t in full_trades}
    truncated_keys = {(t.entry_timestamp, t.direction, round(t.levels.entry, 6)) for t in truncated_trades}
    assert truncated_keys == full_keys
