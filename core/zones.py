"""Supply/demand zone detection (spec section 6.2): a base of small
candles between a large leg-in candle and a large, fast leg-out candle.
Operates on closed bars of the zone timeframe only."""

from dataclasses import dataclass, replace
from enum import Enum

from core.atr import compute_atr_series
from core.candles import Candle
from rulebook.models import ZoneRules


class ZoneType(str, Enum):
    DEMAND = "demand"
    SUPPLY = "supply"


class ZoneStatus(str, Enum):
    ACTIVE = "active"
    BROKEN = "broken"
    EXPIRED = "expired"
    RETIRED = "retired"  # produced a trade, or hit max_touches


@dataclass
class Zone:
    zone_type: ZoneType
    leg_in_index: int
    base_start_index: int
    base_end_index: int
    leg_out_index: int
    proximal: float
    distal: float
    close_line: float
    created_at: str
    status: ZoneStatus = ZoneStatus.ACTIVE
    touches: int = 0
    outside: bool = True  # True once price has left the zone at least once (arms touch-counting)
    consecutive_break_bars: int = 0

    def contains(self, low: float, high: float) -> bool:
        lo, hi = (self.distal, self.proximal) if self.zone_type == ZoneType.DEMAND else (self.proximal, self.distal)
        return high >= lo and low <= hi

    def beyond_distal(self, price: float) -> bool:
        return price < self.distal if self.zone_type == ZoneType.DEMAND else price > self.distal

    def beyond_close_line(self, close: float) -> bool:
        return close < self.close_line if self.zone_type == ZoneType.DEMAND else close > self.close_line


def _base_ok(candles: list[Candle], atrs: list[float | None], start: int, end: int, rules: ZoneRules) -> bool:
    """candles[start:end+1] is a candidate base -- every bar's body must be
    small relative to ATR, and the base's total range must be tight."""
    bars = candles[start: end + 1]
    for i in range(start, end + 1):
        atr = atrs[i]
        if atr is None or atr == 0:
            return False
        if candles[i].body > rules.base_body_atr * atr:
            return False
    base_range = max(b.high for b in bars) - min(b.low for b in bars)
    end_atr = atrs[end]
    if end_atr is None or end_atr == 0:
        return False
    return base_range <= rules.base_range_atr * end_atr


def _is_leg(candle: Candle, atr: float | None, rules: ZoneRules) -> bool:
    return atr is not None and atr > 0 and candle.body >= rules.leg_atr * atr


def detect_zones(candles: list[Candle], rules: ZoneRules) -> list[Zone]:
    """Scans the whole series once. Deliberately simple O(n * max_base):
    for every candidate leg-in bar, try base lengths 1..max_base, and if
    a valid base + leg-out fast-move is found, emit one zone (the
    shortest valid base starting there) and continue scanning after the
    leg-out bar."""
    atrs = compute_atr_series(candles, rules.atr_period)
    zones: list[Zone] = []
    n = len(candles)
    i = 0
    while i < n - 1:
        leg_in = candles[i]
        if not _is_leg(leg_in, atrs[i], rules):
            i += 1
            continue

        found = False
        for base_len in range(1, rules.max_base + 1):
            base_start = i + 1
            base_end = base_start + base_len - 1
            leg_out_idx = base_end + 1
            if leg_out_idx >= n:
                break
            if not _base_ok(candles, atrs, base_start, base_end, rules):
                continue
            leg_out = candles[leg_out_idx]
            if not _is_leg(leg_out, atrs[leg_out_idx], rules):
                continue

            zone = _build_zone(candles, atrs, i, base_start, base_end, leg_out_idx, rules)
            if zone is not None:
                zones.append(zone)
                i = leg_out_idx + 1
                found = True
                break
        if not found:
            i += 1

    return zones


def _build_zone(
    candles: list[Candle], atrs: list[float | None],
    leg_in_idx: int, base_start: int, base_end: int, leg_out_idx: int,
    rules: ZoneRules,
) -> Zone | None:
    leg_in, leg_out = candles[leg_in_idx], candles[leg_out_idx]
    leg_in_up, leg_out_up = leg_in.bullish, leg_out.bullish

    if leg_in_up and leg_out_up:
        zone_type = ZoneType.DEMAND  # UBU
    elif not leg_in_up and leg_out_up:
        zone_type = ZoneType.DEMAND  # DBU
    elif leg_in_up and not leg_out_up:
        zone_type = ZoneType.SUPPLY  # UBD
    else:
        zone_type = ZoneType.SUPPLY  # DBD

    # Fast-mover check: within leg_out_bars, price must close departure_atr
    # beyond the base in the leg-out direction.
    base_bars = candles[base_start: base_end + 1]
    base_high = max(b.high for b in base_bars)
    base_low = min(b.low for b in base_bars)
    window_end = min(leg_out_idx + rules.leg_out_bars, len(candles) - 1)
    atr = atrs[leg_out_idx]
    if atr is None or atr == 0:
        return None
    departed = False
    for j in range(leg_out_idx, window_end + 1):
        close = candles[j].close
        if zone_type == ZoneType.DEMAND and close >= base_high + rules.departure_atr * atr:
            departed = True
            break
        if zone_type == ZoneType.SUPPLY and close <= base_low - rules.departure_atr * atr:
            departed = True
            break
    if not departed:
        return None

    proximal = base_high if zone_type == ZoneType.DEMAND else base_low
    distal = base_low if zone_type == ZoneType.DEMAND else base_high
    close_line = (
        min(b.close for b in base_bars) if zone_type == ZoneType.DEMAND
        else max(b.close for b in base_bars)
    )

    return Zone(
        zone_type=zone_type,
        leg_in_index=leg_in_idx,
        base_start_index=base_start,
        base_end_index=base_end,
        leg_out_index=leg_out_idx,
        proximal=proximal,
        distal=distal,
        close_line=close_line,
        created_at=candles[leg_out_idx].timestamp,
    )


def update_zone_lifecycle(zone: Zone, candle: Candle, candle_index: int, rules: ZoneRules, atr: float | None) -> Zone:
    """Advance one zone's lifecycle by one closed bar. Pure (returns a
    new/updated Zone, does not mutate in place) so callers can diff
    state transitions for logging."""
    if zone.status != ZoneStatus.ACTIVE:
        return zone

    if candle_index - zone.leg_out_index > rules.zone_max_age:
        return replace(zone, status=ZoneStatus.EXPIRED)

    in_zone = zone.contains(candle.low, candle.high)
    broke_atr = atr is not None and atr > 0 and (
        (candle.close < zone.distal - rules.break_atr * atr) if zone.zone_type == ZoneType.DEMAND
        else (candle.close > zone.distal + rules.break_atr * atr)
    )
    broke_close = zone.beyond_distal(candle.close)

    if broke_atr:
        return replace(zone, status=ZoneStatus.BROKEN)

    consecutive = zone.consecutive_break_bars + 1 if broke_close else 0
    if consecutive >= rules.break_bars:
        return replace(zone, status=ZoneStatus.BROKEN)

    touches = zone.touches
    outside = zone.outside
    if in_zone and outside:
        touches += 1
        outside = False
        if touches > rules.max_touches:
            return replace(zone, status=ZoneStatus.RETIRED, touches=touches)
    elif not in_zone:
        outside = True

    return replace(zone, touches=touches, outside=outside, consecutive_break_bars=consecutive)
