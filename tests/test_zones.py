from core.candles import Candle
from core.zones import detect_zones, update_zone_lifecycle, ZoneType, ZoneStatus
from core.atr import compute_atr_series
from rulebook.models import ZoneRules


def mk(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c)


RULES = ZoneRules(
    max_base=3, base_body_atr=0.5, base_range_atr=1.5, leg_atr=1.0,
    leg_out_bars=2, departure_atr=1.5, max_touches=3, break_atr=1.0,
    break_bars=2, zone_max_age=100, atr_period=3,
)


def _warmup(n=5, price=1.0, step=0.001):
    """A few bars of mild chop to seed ATR before the pattern of interest."""
    bars = []
    for i in range(n):
        o = price + i * step
        bars.append(mk(f"2024-01-01 {i:02d}:00:00", o, o + 0.002, o - 0.002, o + 0.0005))
    return bars


def _ts(i):
    return f"2024-02-01 {i:02d}:00:00"


def test_detects_dbu_demand_zone():
    # warmup -> big bearish leg-in -> small base -> big bullish leg-out that departs fast
    candles = _warmup()
    base = len(candles)
    candles += [
        mk(_ts(0), 1.010, 1.011, 0.990, 0.991),   # leg-in: big bearish
        mk(_ts(1), 0.991, 0.993, 0.989, 0.992),   # base (small body)
        mk(_ts(2), 0.992, 0.994, 0.990, 0.991),   # base
        mk(_ts(3), 0.991, 1.015, 0.990, 1.014),   # leg-out: big bullish, departs fast
        mk(_ts(4), 1.014, 1.030, 1.013, 1.029),   # continues departure
    ]
    zones = detect_zones(candles, RULES)
    demand_zones = [z for z in zones if z.zone_type == ZoneType.DEMAND]
    assert len(demand_zones) >= 1
    z = demand_zones[0]
    assert z.leg_in_index == base
    assert z.proximal == max(candles[base + 1].high, candles[base + 2].high)
    assert z.distal == min(candles[base + 1].low, candles[base + 2].low)


def test_detects_ubd_supply_zone():
    candles = _warmup()
    base = len(candles)
    candles += [
        mk(_ts(0), 0.990, 1.011, 0.989, 1.010),   # leg-in: big bullish
        mk(_ts(1), 1.010, 1.012, 1.008, 1.009),   # base
        mk(_ts(2), 1.009, 1.011, 1.007, 1.008),   # base
        mk(_ts(3), 1.009, 1.010, 0.985, 0.986),   # leg-out: big bearish, departs fast
        mk(_ts(4), 0.986, 0.987, 0.970, 0.971),
    ]
    zones = detect_zones(candles, RULES)
    supply_zones = [z for z in zones if z.zone_type == ZoneType.SUPPLY]
    assert len(supply_zones) >= 1
    z = supply_zones[0]
    assert z.proximal == min(candles[base + 1].low, candles[base + 2].low)
    assert z.distal == max(candles[base + 1].high, candles[base + 2].high)


def test_detects_ubu_demand_zone_continuation():
    candles = _warmup()
    candles += [
        mk(_ts(0), 0.990, 1.011, 0.989, 1.010),   # leg-in: big bullish (uptrend)
        mk(_ts(1), 1.010, 1.012, 1.008, 1.009),   # base
        mk(_ts(2), 1.009, 1.011, 1.007, 1.008),   # base
        mk(_ts(3), 1.009, 1.030, 1.008, 1.029),   # leg-out: big bullish continuation
        mk(_ts(4), 1.029, 1.045, 1.028, 1.044),
    ]
    zones = detect_zones(candles, RULES)
    assert any(z.zone_type == ZoneType.DEMAND for z in zones)


def test_detects_dbd_supply_zone_continuation():
    candles = _warmup()
    candles += [
        mk(_ts(0), 1.010, 1.011, 0.990, 0.991),   # leg-in: big bearish (downtrend)
        mk(_ts(1), 0.991, 0.993, 0.989, 0.992),   # base
        mk(_ts(2), 0.992, 0.994, 0.990, 0.991),   # base
        mk(_ts(3), 0.991, 0.992, 0.970, 0.971),   # leg-out: big bearish continuation
        mk(_ts(4), 0.971, 0.972, 0.955, 0.956),
    ]
    zones = detect_zones(candles, RULES)
    assert any(z.zone_type == ZoneType.SUPPLY for z in zones)


def test_no_zone_when_base_too_wide():
    candles = _warmup()
    candles += [
        mk(_ts(0), 1.010, 1.011, 0.990, 0.991),
        mk(_ts(1), 0.991, 1.040, 0.950, 0.995),   # huge-range "base" candle -- not a valid base
        mk(_ts(2), 0.995, 1.015, 0.990, 1.014),
        mk(_ts(3), 1.014, 1.030, 1.013, 1.029),
    ]
    zones = detect_zones(candles, RULES)
    assert zones == []


def test_zone_lifecycle_touch_and_retire():
    candles = _warmup()
    candles += [
        mk(_ts(0), 1.010, 1.011, 0.990, 0.991),
        mk(_ts(1), 0.991, 0.993, 0.989, 0.992),
        mk(_ts(2), 0.992, 0.994, 0.990, 0.991),
        mk(_ts(3), 0.991, 1.015, 0.990, 1.014),
        mk(_ts(4), 1.014, 1.030, 1.013, 1.029),
    ]
    zones = detect_zones(candles, RULES)
    z = next(zz for zz in zones if zz.zone_type == ZoneType.DEMAND)
    atrs_full = compute_atr_series(candles, RULES.atr_period)

    # Price leaves the zone (already did, via leg-out), then returns: touch 1
    touch_candle = mk(_ts(5), 1.000, 1.001, 0.991, 0.993)
    z1 = update_zone_lifecycle(z, touch_candle, len(candles), RULES, atrs_full[-1] if atrs_full else None)
    assert z1.touches == 1
    assert z1.status == ZoneStatus.ACTIVE


def test_zone_breaks_on_close_beyond_distal_for_break_bars():
    candles = _warmup()
    candles += [
        mk(_ts(0), 1.010, 1.011, 0.990, 0.991),
        mk(_ts(1), 0.991, 0.993, 0.989, 0.992),
        mk(_ts(2), 0.992, 0.994, 0.990, 0.991),
        mk(_ts(3), 0.991, 1.015, 0.990, 1.014),
        mk(_ts(4), 1.014, 1.030, 1.013, 1.029),
    ]
    zones = detect_zones(candles, RULES)
    z = next(zz for zz in zones if zz.zone_type == ZoneType.DEMAND)

    below = mk(_ts(5), 0.985, 0.986, 0.970, 0.975)  # closes well below distal
    atr = 0.01
    z1 = update_zone_lifecycle(z, below, len(candles), RULES, atr)
    z2 = update_zone_lifecycle(z1, below, len(candles) + 1, RULES, atr)
    assert z2.status == ZoneStatus.BROKEN


def test_zone_expires_after_max_age():
    candles = _warmup()
    candles += [
        mk(_ts(0), 1.010, 1.011, 0.990, 0.991),
        mk(_ts(1), 0.991, 0.993, 0.989, 0.992),
        mk(_ts(2), 0.992, 0.994, 0.990, 0.991),
        mk(_ts(3), 0.991, 1.015, 0.990, 1.014),
        mk(_ts(4), 1.014, 1.030, 1.013, 1.029),
    ]
    zones = detect_zones(candles, RULES)
    z = next(zz for zz in zones if zz.zone_type == ZoneType.DEMAND)
    neutral = mk(_ts(5), 1.020, 1.021, 1.019, 1.020)
    far_index = z.leg_out_index + RULES.zone_max_age + 1
    z1 = update_zone_lifecycle(z, neutral, far_index, RULES, 0.01)
    assert z1.status == ZoneStatus.EXPIRED
