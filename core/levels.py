"""Trade level calculation: entry, stop loss, target, RR gate (spec 6.5)."""

import math
from dataclasses import dataclass

from core.candles import Candle
from core.trend import Pivot, PivotKind
from core.zones import Zone, ZoneType
from rulebook.models import RiskRules


@dataclass(frozen=True)
class TradeLevels:
    entry: float
    sl: float
    target: float
    rr: float
    direction: str  # "long" | "short"


def spread_for(pair: str, risk: RiskRules) -> float:
    return risk.spreads.get(pair, risk.default_spread)


def round_step_for(pair: str, risk: RiskRules) -> float:
    return risk.round_step.get(pair, risk.round_step.get("default", 0.0100))


def compute_sl(
    direction: str, swing_candles: list[Candle], sl_buffer: float, spread: float,
) -> float:
    """SL beyond the extreme of the trap-through-entry swing (spec: 'the
    swing in the surrounding area, not just the last candle'), plus an
    ATR buffer and the pair's spread."""
    if direction == "long":
        extreme = min(c.low for c in swing_candles)
        return extreme - sl_buffer - spread
    extreme = max(c.high for c in swing_candles)
    return extreme + sl_buffer + spread


def nearest_opposing_target(
    direction: str, entry: float, zones: list[Zone], pivots: list[Pivot],
    *, round_step: float | None = None,
) -> float | None:
    """Nearest opposing level beyond entry: the proximal edge of the
    nearest opposite-type active zone, the last confirmed opposite-kind
    pivot, or (if round_step given) the next round number. Returns the
    CLOSEST of whichever candidates exist beyond entry in the trade
    direction, or None if nothing qualifies."""
    candidates: list[float] = []
    opposite_zone_type = ZoneType.SUPPLY if direction == "long" else ZoneType.DEMAND
    for z in zones:
        if z.zone_type != opposite_zone_type:
            continue
        level = z.proximal
        if (direction == "long" and level > entry) or (direction == "short" and level < entry):
            candidates.append(level)

    # Spec 6.5: only the LAST confirmed opposite-kind pivot. Scanning every
    # historical pivot made targets depend on how much history was loaded.
    opposite_pivot_kind = PivotKind.HIGH if direction == "long" else PivotKind.LOW
    last = next((p for p in reversed(pivots) if p.kind == opposite_pivot_kind), None)
    if last is not None and ((direction == "long" and last.price > entry) or (direction == "short" and last.price < entry)):
        candidates.append(last.price)

    if round_step:
        if direction == "long":
            candidates.append(math.ceil(entry / round_step) * round_step)
        else:
            candidates.append(math.floor(entry / round_step) * round_step)

    if not candidates:
        return None
    return min(candidates) if direction == "long" else max(candidates)


def compute_trade_levels(
    direction: str, entry: float, sl: float, target: float | None,
) -> TradeLevels | None:
    """Returns None (reject) if risk<=0, target missing/behind entry, or
    RR < caller's min_rr (caller applies the min_rr gate itself since it
    varies per rulebook)."""
    risk = (entry - sl) if direction == "long" else (sl - entry)
    if risk <= 0:
        return None
    if target is None:
        return None
    reward = (target - entry) if direction == "long" else (entry - target)
    if reward <= 0:
        return None
    return TradeLevels(entry=entry, sl=sl, target=target, rr=reward / risk, direction=direction)
