from core.candles import Candle
from core.zones import Zone, ZoneType, ZoneStatus
from core.trend import Trend
from core.setup_machine import (
    Setup, SetupState, Grade, setup_direction, advance_setup, check_entry_confirmation,
)
from rulebook.models import SetupRules, TrendRules


def mk(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def demand_zone(proximal=1.00, distal=0.98, close_line=0.985):
    return Zone(
        zone_type=ZoneType.DEMAND, leg_in_index=0, base_start_index=1, base_end_index=2,
        leg_out_index=3, proximal=proximal, distal=distal, close_line=close_line,
        created_at="t", status=ZoneStatus.ACTIVE,
    )


def test_setup_direction_long_for_demand():
    assert setup_direction(demand_zone()) == "long"


def test_watching_to_in_zone_on_touch():
    setup = Setup(zone=demand_zone(), direction="long")
    candle = mk("t5", 1.00, 1.001, 0.99, 0.995)  # dips into the zone
    result = advance_setup(setup, candle, 5, SetupRules(), Trend.RANGE, [candle])
    assert result.state == SetupState.IN_ZONE
    assert result.deepest_price == 0.99


def test_watching_blocked_by_trend_filter():
    setup = Setup(zone=demand_zone(), direction="long")
    candle = mk("t5", 1.00, 1.001, 0.99, 0.995)
    result = advance_setup(setup, candle, 5, SetupRules(), Trend.DOWN, [candle], TrendRules())
    assert result.state == SetupState.WATCHING  # counter-trend long blocked by default


def test_watching_allowed_counter_trend_when_variant_enabled():
    setup = Setup(zone=demand_zone(), direction="long")
    candle = mk("t5", 1.00, 1.001, 0.99, 0.995)
    trend_rules = TrendRules(counter_trend_extreme=True)
    result = advance_setup(setup, candle, 5, SetupRules(), Trend.DOWN, [candle], trend_rules)
    assert result.state == SetupState.IN_ZONE


def test_in_zone_trap_grade_a_confirms_with_pin_bar():
    zone = demand_zone(proximal=1.00, distal=0.98, close_line=0.985)
    setup = Setup(zone=zone, direction="long", state=SetupState.IN_ZONE, deepest_index=5, deepest_price=0.97)
    # bullish pin bar: long lower wick beyond distal, close high in range
    trap_candle = mk("t6", 0.975, 0.978, 0.960, 0.977)
    result = advance_setup(setup, trap_candle, 6, SetupRules(), Trend.RANGE, [trap_candle])
    assert result.state == SetupState.ZONE_CONFIRMED
    assert result.grade == Grade.A


def test_in_zone_grade_c_rejected_discards_setup():
    zone = demand_zone(proximal=1.00, distal=0.98, close_line=0.985)
    # deepest price stays clearly in the proximal half (midpoint is 0.99)
    setup = Setup(zone=zone, direction="long", state=SetupState.IN_ZONE, deepest_index=5, deepest_price=0.998)
    # clear bullish pin bar, but it never pushes the deepest price past the midpoint
    trap_candle = mk("t6", 0.997, 0.999, 0.993, 0.9985)
    result = advance_setup(setup, trap_candle, 6, SetupRules(), Trend.RANGE, [trap_candle], TrendRules())
    assert result.state == SetupState.EXPIRED
    assert result.grade == Grade.C


def test_in_zone_expires_after_confirm_window():
    zone = demand_zone()
    setup = Setup(zone=zone, direction="long", state=SetupState.IN_ZONE, deepest_index=0, deepest_price=0.97)
    neutral = mk("t10", 1.00, 1.001, 0.999, 1.0005)
    result = advance_setup(setup, neutral, 10, SetupRules(confirm_window=3), Trend.RANGE, [neutral])
    assert result.state == SetupState.EXPIRED
    assert result.expiry_reason == "confirm_window_elapsed"


def test_zone_confirmed_enters_on_confirmation_tf_pattern():
    zone = demand_zone()
    setup = Setup(zone=zone, direction="long", state=SetupState.ZONE_CONFIRMED, grade=Grade.A, confirmed_at_index=6)
    confirm_candles = [mk("c1", 0.975, 0.978, 0.960, 0.977)]  # bullish pin bar on confirm TF
    result = check_entry_confirmation(setup, confirm_candles, SetupRules())
    assert result.state == SetupState.ENTRY
    assert result.entry_index == 0


def test_zone_confirmed_expires_without_confirmation_tf_pattern():
    zone = demand_zone()
    setup = Setup(zone=zone, direction="long", state=SetupState.ZONE_CONFIRMED, grade=Grade.A, confirmed_at_index=6)
    neutral = [mk("c1", 1.0, 1.001, 0.999, 1.0005), mk("c2", 1.0005, 1.0015, 0.9995, 1.001)]
    rules = SetupRules(h4_window=2)
    result = check_entry_confirmation(setup, neutral, rules)
    assert result.state == SetupState.EXPIRED
    assert result.expiry_reason == "no_confirmation_tf_confirmation"


def test_close_trap_counts_any_close_beyond_close_line_during_retrace():
    # trap_mode=close: grade A if ANY bar during the retrace closed beyond the
    # close line (spec 6.4), not just the confirmation candle itself.
    zone = demand_zone(proximal=1.00, distal=0.98, close_line=0.985)
    rules = SetupRules(trap_mode="close", accepted_grades=("A",))
    setup = Setup(zone=zone, direction="long")
    t5 = mk("t5", 1.002, 1.003, 0.995, 0.996)
    setup = advance_setup(setup, t5, 5, rules, Trend.RANGE, [t5])
    t6 = mk("t6", 0.990, 0.990, 0.975, 0.978)  # closes below the close line
    setup = advance_setup(setup, t6, 6, rules, Trend.RANGE, [t5, t6])
    assert setup.state == SetupState.IN_ZONE
    t7 = mk("t7", 0.978, 0.995, 0.977, 0.994)  # bullish engulfing back above the close line
    setup = advance_setup(setup, t7, 7, rules, Trend.RANGE, [t5, t6, t7])
    assert setup.state == SetupState.ZONE_CONFIRMED
    assert setup.grade == Grade.A
