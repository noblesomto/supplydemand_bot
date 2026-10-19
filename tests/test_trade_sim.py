from core.candles import Candle
from core.levels import TradeLevels
from core.trade_sim import simulate_outcome, r_multiple
from rulebook.models import ExitRules


def mk(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def test_r_multiple_long():
    assert r_multiple(1.03, entry=1.00, sl=0.99, direction="long") == 3.0


def test_fixed_2r_hits_target():
    levels = TradeLevels(entry=1.00, sl=0.99, target=1.05, rr=5.0, direction="long")
    candles = [mk("t1", 1.00, 1.03, 0.995, 1.025), mk("t2", 1.025, 1.03, 1.00, 1.02)]
    outcome = simulate_outcome(levels, "long", candles, ExitRules(model="fixed_2r"))
    assert outcome.resolved
    assert outcome.realized_r == 2.0
    assert outcome.exit_kind == "target"


def test_fixed_2r_hits_sl_first_on_same_bar():
    levels = TradeLevels(entry=1.00, sl=0.99, target=1.05, rr=5.0, direction="long")
    candles = [mk("t1", 1.00, 1.03, 0.985, 1.00)]  # touches both SL and 2R target in one bar
    outcome = simulate_outcome(levels, "long", candles, ExitRules(model="fixed_2r"))
    assert outcome.realized_r == -1.0
    assert outcome.exit_kind == "sl"


def test_unresolved_when_neither_hit():
    levels = TradeLevels(entry=1.00, sl=0.99, target=1.05, rr=5.0, direction="long")
    candles = [mk("t1", 1.00, 1.005, 0.995, 1.001)]
    outcome = simulate_outcome(levels, "long", candles, ExitRules(model="fixed_2r"))
    assert not outcome.resolved


def test_short_fixed_target():
    levels = TradeLevels(entry=1.00, sl=1.01, target=0.95, rr=5.0, direction="short")
    candles = [mk("t1", 1.00, 1.005, 0.94, 0.945)]
    outcome = simulate_outcome(levels, "short", candles, ExitRules(model="next_level"))
    assert outcome.resolved
    assert outcome.exit_kind == "target"
    assert outcome.realized_r > 0


def test_ladder_trails_stop_and_exits_on_trailed_sl():
    levels = TradeLevels(entry=1.00, sl=0.99, target=1.10, rr=10.0, direction="long")
    candles = [
        mk("t1", 1.00, 1.03, 0.995, 1.025),   # closes beyond level 1.02
        mk("t2", 1.025, 1.04, 1.021, 1.03),   # holds above 1.02 -> SL trails to ~1.02
        mk("t3", 1.03, 1.035, 1.015, 1.018),  # drops back through trailed SL
    ]
    outcome = simulate_outcome(
        levels, "long", candles, ExitRules(model="ladder"),
        opposing_levels=[1.02], sl_buffer=0.001,
    )
    assert outcome.resolved
    assert outcome.exit_kind == "sl_trailed"
    assert outcome.realized_r > 0  # exited in profit, not at the original -1R stop


def test_ladder_never_moves_sl_to_breakeven_early():
    levels = TradeLevels(entry=1.00, sl=0.99, target=1.10, rr=10.0, direction="long")
    candles = [
        mk("t1", 1.00, 1.01, 0.995, 1.005),  # small favorable move, no level reached
        mk("t2", 1.005, 1.006, 0.985, 0.986),  # reverses straight through original SL
    ]
    outcome = simulate_outcome(
        levels, "long", candles, ExitRules(model="ladder"),
        opposing_levels=[1.05], sl_buffer=0.001,
    )
    assert outcome.realized_r == -1.0  # original SL, not breakeven


def test_ladder_ignores_levels_behind_entry_and_never_loosens_sl():
    # A level below entry (and below the original SL) must not drag the stop down:
    # the worst a ladder trade can do is the original -1R.
    levels = TradeLevels(entry=1.00, sl=0.99, target=1.05, rr=5.0, direction="long")
    candles = [mk("t1", 1.00, 1.005, 0.995, 1.002), mk("t2", 1.002, 1.003, 0.90, 0.91)]
    outcome = simulate_outcome(levels, "long", candles, ExitRules(model="ladder"),
                               opposing_levels=[0.95], sl_buffer=0.001)
    assert outcome.realized_r == -1.0
    assert outcome.exit_kind == "sl"


def test_short_ladder_ignores_levels_behind_entry():
    levels = TradeLevels(entry=1.00, sl=1.01, target=0.95, rr=5.0, direction="short")
    candles = [mk("t1", 1.00, 1.005, 0.995, 0.998), mk("t2", 0.998, 1.10, 0.997, 1.09)]
    outcome = simulate_outcome(levels, "short", candles, ExitRules(model="ladder"),
                               opposing_levels=[1.05], sl_buffer=0.001)
    assert outcome.realized_r == -1.0


def test_net_of_spread_charges_spread_over_sl_distance():
    from core.trade_sim import TradeOutcome, net_of_spread
    gross = TradeOutcome(True, 2.0, "target", "t2")
    net = net_of_spread(gross, entry=1.00, sl=0.99, spread=0.0005)  # 0.0005 / 0.01 = 0.05R
    assert abs(net.realized_r - 1.95) < 1e-9
    assert net.exit_kind == "target"


def test_net_of_spread_leaves_unresolved_outcome_alone():
    from core.trade_sim import TradeOutcome, net_of_spread
    gross = TradeOutcome(False, None, None, None)
    assert net_of_spread(gross, entry=1.00, sl=0.99, spread=0.0005) == gross
