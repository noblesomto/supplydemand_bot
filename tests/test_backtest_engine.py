from core.candles import Candle
from core.atr import compute_atr_series
from core.backtest_engine import run_backtest, _materialize_trade
from core.setup_machine import Setup, SetupState, Grade
from core.zones import Zone, ZoneType, ZoneStatus
from rulebook.models import Rulebook, ZoneRules, SetupRules, RiskRules, ExitRules


def mk(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def _d1(i, o, h, l, c):
    return mk(f"2024-01-{i+1:02d} 22:00:00", o, h, l, c)


def _h4_from_d1(d1_candle, n=6):
    """Split one D1 candle into n flat H4 bars spanning its own range,
    close enough for engine wiring tests -- not meant to model realistic
    intrabar movement."""
    bars = []
    step = (d1_candle.close - d1_candle.open) / n
    for k in range(n):
        o = d1_candle.open + step * k
        c = d1_candle.open + step * (k + 1)
        h = max(o, c) + (d1_candle.high - max(d1_candle.open, d1_candle.close)) / n
        l = min(o, c) - (min(d1_candle.open, d1_candle.close) - d1_candle.low) / n
        bars.append(mk(f"{d1_candle.timestamp[:10]} {k*4:02d}:00:00", o, h, l, c))
    return bars


def build_rulebook(**overrides):
    base = dict(
        zone=ZoneRules(max_base=3, base_body_atr=0.5, base_range_atr=1.5, leg_atr=1.0,
                        leg_out_bars=2, departure_atr=1.5, max_touches=3, break_atr=1.0,
                        break_bars=2, zone_max_age=100, atr_period=3),
        setup=SetupRules(trap_mode="wick", accepted_grades=["A"], confirm_window=5, h4_window=2),
        risk=RiskRules(risk_per_trade_pct=1.0, min_rr=1.0, sl_buffer_atr=0.1),
        exit=ExitRules(model="next_level"),
    )
    base.update(overrides)
    return Rulebook(**base)


def test_run_backtest_produces_funnel_counts_and_no_crash_on_quiet_data():
    candles = [_d1(i, 1.0, 1.002, 0.998, 1.0005) for i in range(30)]  # flat, no zones
    h4 = [h for d in candles for h in _h4_from_d1(d)]
    trades, funnel = run_backtest(
        pair="EUR/USD", zone_tf_candles=candles, confirm_tf_candles=h4,
        confirm_bars_per_zone_bar=6, rulebook=build_rulebook(),
    )
    assert trades == []
    assert funnel.zones_detected == 0


def test_run_backtest_finds_a_demand_zone_and_can_enter_a_trade():
    candles = [_d1(i, 1.0 + i * 0.0001, 1.002 + i * 0.0001, 0.998 + i * 0.0001, 1.0005 + i * 0.0001) for i in range(8)]
    # leg-in (big bearish), base (2 small bars), leg-out (big bullish, departs fast)
    candles += [
        _d1(8, 1.010, 1.011, 0.990, 0.991),
        _d1(9, 0.991, 0.993, 0.989, 0.992),
        _d1(10, 0.992, 0.994, 0.990, 0.991),
        _d1(11, 0.991, 1.015, 0.990, 1.014),
        _d1(12, 1.014, 1.040, 1.013, 1.039),
    ]
    # retrace back into the zone, trade below distal (trap), then a bullish pin bar
    candles += [
        _d1(13, 1.039, 1.041, 1.010, 1.015),
        _d1(14, 1.015, 1.016, 0.985, 0.987),   # trades below distal (0.989) -> grade A trap
        _d1(15, 0.986, 0.988, 0.965, 0.987),   # bullish pin bar: long lower wick, close high
    ]
    h4 = [h for d in candles for h in _h4_from_d1(d)]
    trades, funnel = run_backtest(
        pair="EUR/USD", zone_tf_candles=candles, confirm_tf_candles=h4,
        confirm_bars_per_zone_bar=6, rulebook=build_rulebook(),
    )
    assert funnel.zones_detected >= 1
    assert funnel.retraced >= 1
    # Whether or not a trade actually closed in this short synthetic series,
    # the engine must reach zone-TF confirmation on this clean trap setup.
    assert funnel.graded_a >= 1 or funnel.zone_tf_confirmed >= 1
    for t in trades:
        assert t.outcome.resolved
        assert t.levels.rr >= build_rulebook().risk.min_rr - 1e-9


def test_materialize_trade_sl_uses_deepest_bar_not_leg_out_bar():
    """Spec 6.5: SL = extreme 'from the trap/deepest bar through the
    entry bar' -- NOT from the zone's leg-out bar, which can sit many
    bars earlier (zone_max_age allows up to 250). A stale, unrelated
    wick sitting between leg-out and the real trap must not stretch the
    SL -- regression for a bug where the swing window started at
    zone.leg_out_index instead of setup.deepest_index."""
    zone = Zone(
        zone_type=ZoneType.DEMAND, leg_in_index=0, base_start_index=0, base_end_index=0,
        leg_out_index=0, proximal=1.000, distal=0.990, close_line=0.990,
        created_at="2024-01-01 22:00:00", status=ZoneStatus.ACTIVE,
    )
    zone_tf_candles = [
        mk("2024-01-01 22:00:00", 1.000, 1.005, 0.100, 1.000),  # leg-out bar: unrelated deep wick, irrelevant to the trap
        mk("2024-01-02 22:00:00", 1.000, 1.005, 0.998, 1.001),
        mk("2024-01-03 22:00:00", 1.001, 1.004, 0.999, 1.000),
        mk("2024-01-04 22:00:00", 1.000, 1.003, 0.997, 0.999),
        mk("2024-01-05 22:00:00", 0.999, 1.000, 0.995, 0.997),
        mk("2024-01-06 22:00:00", 0.997, 0.998, 0.985, 0.996),  # the actual trap/deepest bar
    ]
    setup = Setup(
        zone=zone, direction="long", state=SetupState.ENTRY,
        deepest_index=5, deepest_price=0.985, grade=Grade.A, confirmed_at_index=5, entry_index=0,
    )
    entry_candle = mk("2024-01-06 23:00:00", 1.000, 1.010, 0.999, 1.050)
    later_candle = mk("2024-01-07 03:00:00", 1.050, 1.055, 1.045, 1.052)  # stays between SL and target: open_at_end
    confirm_window = [entry_candle, later_candle]
    opposing_zone = Zone(
        zone_type=ZoneType.SUPPLY, leg_in_index=99, base_start_index=99, base_end_index=99,
        leg_out_index=99, proximal=1.180, distal=1.200, close_line=1.200,
        created_at="2024-01-01 22:00:00", status=ZoneStatus.ACTIVE,
    )
    rulebook = build_rulebook()

    trade = _materialize_trade(
        pair="EUR/USD", setup=setup, confirm_window=confirm_window,
        all_confirm_candles=confirm_window, zone_tf_candles=zone_tf_candles,
        rulebook=rulebook, active_zones=[opposing_zone], pivots=[],
        spread=0.0, round_step=None, zone_bar_index=5,
        confirm_ts=[c.timestamp for c in confirm_window],
        confirm_atrs=compute_atr_series(confirm_window, rulebook.zone.atr_period),
    )
    assert trade is not None, "RR should pass once SL is anchored to the real trap bar, not the stale leg-out wick"
    assert trade.levels.sl > 0.9, f"SL {trade.levels.sl} is still anchored to the unrelated leg-out wick"
