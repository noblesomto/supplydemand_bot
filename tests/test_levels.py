from core.candles import Candle
from core.levels import compute_sl, nearest_opposing_target, compute_trade_levels
from core.trend import Pivot, PivotKind
from core.zones import Zone, ZoneType, ZoneStatus


def mk(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


def test_compute_sl_long_uses_swing_extreme_plus_buffer_and_spread():
    swing = [mk("t1", 1.0, 1.01, 0.98, 1.0), mk("t2", 1.0, 1.02, 0.985, 1.01)]
    sl = compute_sl("long", swing, sl_buffer=0.001, spread=0.0001)
    assert sl == 0.98 - 0.001 - 0.0001


def test_compute_sl_short_uses_swing_extreme_plus_buffer_and_spread():
    swing = [mk("t1", 1.0, 1.02, 0.98, 1.0), mk("t2", 1.0, 1.015, 0.99, 0.995)]
    sl = compute_sl("short", swing, sl_buffer=0.001, spread=0.0001)
    assert sl == 1.02 + 0.001 + 0.0001


def _zone(zone_type, proximal, distal):
    return Zone(
        zone_type=zone_type, leg_in_index=0, base_start_index=1, base_end_index=2,
        leg_out_index=3, proximal=proximal, distal=distal, close_line=proximal,
        created_at="t", status=ZoneStatus.ACTIVE,
    )


def test_nearest_opposing_target_long_picks_closest_supply_zone():
    zones = [_zone(ZoneType.SUPPLY, 1.05, 1.06), _zone(ZoneType.SUPPLY, 1.08, 1.09)]
    target = nearest_opposing_target("long", entry=1.00, zones=zones, pivots=[])
    assert target == 1.05


def test_nearest_opposing_target_short_picks_closest_demand_zone():
    zones = [_zone(ZoneType.DEMAND, 0.95, 0.94), _zone(ZoneType.DEMAND, 0.90, 0.89)]
    target = nearest_opposing_target("short", entry=1.00, zones=zones, pivots=[])
    assert target == 0.95


def test_nearest_opposing_target_ignores_zones_behind_entry():
    zones = [_zone(ZoneType.SUPPLY, 0.90, 0.91)]  # behind a long entry at 1.00
    target = nearest_opposing_target("long", entry=1.00, zones=zones, pivots=[])
    assert target is None


def test_nearest_opposing_target_uses_pivot_when_closer():
    zones = [_zone(ZoneType.SUPPLY, 1.10, 1.11)]
    pivots = [Pivot(index=5, timestamp="t", price=1.03, kind=PivotKind.HIGH)]
    target = nearest_opposing_target("long", entry=1.00, zones=zones, pivots=pivots)
    assert target == 1.03


def test_nearest_opposing_target_round_number():
    target = nearest_opposing_target("long", entry=1.0932, zones=[], pivots=[], round_step=0.01)
    assert abs(target - 1.10) < 1e-9


def test_compute_trade_levels_rejects_non_positive_risk():
    assert compute_trade_levels("long", entry=1.00, sl=1.00, target=1.05) is None


def test_compute_trade_levels_rejects_target_behind_entry():
    assert compute_trade_levels("long", entry=1.00, sl=0.99, target=0.995) is None


def test_compute_trade_levels_computes_rr():
    levels = compute_trade_levels("long", entry=1.00, sl=0.99, target=1.03)
    assert levels.rr == 3.0


def test_nearest_opposing_target_uses_only_last_confirmed_pivot():
    # Spec 6.5: the LAST confirmed opposite pivot, not any historical one --
    # otherwise targets depend on how much history happens to be loaded.
    pivots = [
        Pivot(index=1, timestamp="t1", price=1.02, kind=PivotKind.HIGH),  # stale, closer
        Pivot(index=9, timestamp="t9", price=1.06, kind=PivotKind.HIGH),  # most recent
        Pivot(index=10, timestamp="t10", price=0.95, kind=PivotKind.LOW),
    ]
    target = nearest_opposing_target("long", entry=1.00, zones=[], pivots=pivots)
    assert target == 1.06


def test_nearest_opposing_target_ignores_last_pivot_behind_entry():
    pivots = [
        Pivot(index=1, timestamp="t1", price=1.05, kind=PivotKind.HIGH),
        Pivot(index=9, timestamp="t9", price=0.99, kind=PivotKind.HIGH),  # most recent, behind entry
    ]
    assert nearest_opposing_target("long", entry=1.00, zones=[], pivots=pivots) is None
